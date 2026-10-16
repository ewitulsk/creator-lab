"""Faster Llama word features (a runtime patch: neuralset itself is not edited).

neuralset's HuggingFaceText embeds each word with its preceding context (up to 1,024
tokens). For every batch it copies the hidden states of all 29 layers, for every token of
every context, to the CPU (float32 after the half-precision patch: ~1.5 GB per batch of
4), stacks them again on the CPU, and only then picks out each word's few tokens. The GPU
sits idle most of the time.

This replaces the body of `HuggingFaceText._get_data` (the function exca's cache wraps, so
cache keys are unchanged; batch_size is excluded from them) with the same loop, except that
each word's tokens are sliced and aggregated on the GPU with neuralset's own
`_aggregate_tokens`, so only the small (layers, dims) result is copied back. Slicing the
half-precision states and casting the slice to float32 gives the same values as casting
everything first. `batch_size` is configurable (the checkpoint config says 4).
"""

from __future__ import annotations

import logging
from collections.abc import Iterator
from dataclasses import dataclass

import numpy as np

log = logging.getLogger("runner.tribe")


@dataclass
class FastTextOptions:
    batch_size: int | None = None  # None: the extractor's own (4 in tribev2's config)


_options = FastTextOptions()
_installed = False


def configure(**kwargs) -> FastTextOptions:
    global _options
    _options = FastTextOptions(**{**_options.__dict__, **kwargs})
    return _options


def word_token_span(n_tokens: int, n_pads: int, n_prefix: int) -> tuple[int, int]:
    """neuralset's selection: drop trailing pads, then keep the last max(1, n_target) tokens."""
    length = n_tokens - n_pads
    n_target = n_tokens - n_pads - n_prefix
    return max(length - max(1, n_target), 0), length


def _strip_cast_output_hook(model) -> None:
    for key, fn in list(model._forward_hooks.items()):
        if getattr(fn, "_creator_lab_cast", False):
            del model._forward_hooks[key]


def fast_text_get_data(self, events) -> Iterator[np.ndarray]:
    """Drop-in body for HuggingFaceText._get_data (see the module doc)."""
    import torch
    from neuralset.extractors import text as nstext
    from torch.utils.data import DataLoader
    from tqdm import tqdm

    batch_size = _options.batch_size or self.batch_size
    dloader = DataLoader(nstext.TextDataset(events), batch_size=batch_size, shuffle=False)
    if len(dloader) > 1:
        dloader = tqdm(dloader, desc="Computing word embeddings")
    device = "auto" if self.device == "accelerate" else self.device
    if device == "auto":
        device = "cuda" if torch.cuda.is_available() else "cpu"
    model = self.model  # loads the model and tokenizer
    _strip_cast_output_hook(model)
    with torch.no_grad():
        for target_words, context in dloader:
            with nstext.environment_variables(TOKENIZERS_PARALLELISM="false"):
                text = context if self.contextualized else target_words
                if isinstance(text, tuple):
                    text = list(text)
                if not all(text):
                    raise ValueError(f"Empty text or context for target_words {target_words!r}")
                inputs = self.tokenizer(
                    text,
                    add_special_tokens=False,
                    return_tensors="pt",
                    padding=True,
                    truncation=True,
                ).to(device)
            outputs = model(**inputs, output_hidden_states=True)
            if "hidden_states" in outputs:
                states = outputs.hidden_states
            else:  # bart (encoder/decoder)
                states = outputs.encoder_hidden_states + outputs.decoder_hidden_states
            ids = inputs["input_ids"].cpu().numpy()
            n_tokens = ids.shape[1]
            for i, target_word in enumerate(target_words):
                n_pads = int((ids[i] == self._pad_id).sum())
                if self.contextualized:
                    prefix = context[i][: -len(target_word)].rstrip()
                    n_prefix = len(self.tokenizer.encode(prefix, add_special_tokens=False)) if prefix else 0
                    start, end = word_token_span(n_tokens, n_pads, n_prefix)
                else:
                    start, end = 0, n_tokens - n_pads
                # (n_layers, tokens, dims) for this word only, float32 like the original path
                word_state = torch.stack([layer[i, start:end] for layer in states]).float()
                out = self._aggregate_tokens(word_state)
                out = out.cpu().numpy() if isinstance(out, torch.Tensor) else np.asarray(out)
                if not self.cache_all_layers and self.cache_n_layers is None:
                    out = self._aggregate_layers(out)
                if np.isnan(out).any():
                    raise ValueError(f"NaN in output for target_word {target_word} with context {context}")
                yield out
            del states, outputs, inputs


def install() -> bool:
    global _installed
    if _installed:
        return True
    from neuralset.extractors import text as nstext

    imethod = nstext.HuggingFaceText.model_fields["infra"].default._infra_method
    if imethod is None:
        log.warning("HuggingFaceText has no infra method; fast text path not installed")
        return False
    original = imethod.method

    def _get_data(self, events):
        yield from fast_text_get_data(self, events)

    _get_data.__name__ = original.__name__
    _get_data.__qualname__ = original.__qualname__
    _get_data.__annotations__ = getattr(original, "__annotations__", {})
    _get_data._creator_lab_original = original
    imethod.method = _get_data
    _installed = True
    return True


def uninstall() -> None:
    global _installed
    from neuralset.extractors import text as nstext

    imethod = nstext.HuggingFaceText.model_fields["infra"].default._infra_method
    original = getattr(imethod.method, "_creator_lab_original", None)
    if original is not None:
        imethod.method = original
    _installed = False
