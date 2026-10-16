# Studies

A study is a pre-registered question with a fixed sample rule, fixed measures and an
analysis computed in the app. Each one is a Python module in `studies/` plus a row in the
database (one row per version).

## Lifecycle

| status | what it means |
|---|---|
| draft | The module's criteria, measures and methodology can still change (the row follows the code). Analyses run on a draft are marked "not pre-registered". |
| locked | Criteria parameters, measures and the module's source hash are frozen. If the module file changes afterwards, the study is flagged and you create a new version. |
| collecting | "Fill gaps" has pulled the members and issued the jobs their requirements need. |
| analyzed | The analysis ran on the locked study. Results record the git commit (and `-dirty` when `studies/` has uncommitted changes) and the module hash. |

Results go to the database and to `results/<slug>/v<version>/` (results.json, figures),
which is small and safe to commit.

## Using a study

1. **Select members** runs the criteria and lists every member with its role (e.g. hit or
   flop), group (e.g. pair) and reason.
2. Read the methodology and measures. When they're right, **Lock**.
3. **Fill gaps** pulls discovered members and issues jobs for every missing requirement
   (downloads are chained automatically). Watch them on Jobs; the member table shows each
   requirement's status.
4. **Analyze** runs the module's analysis in the background.
5. Export members as CSV or Parquet.

In Discover, "Videos needed for" a study runs that study's criteria on the open channel
and shows the videos it would include, with their roles. "Also add to the study" adds
pulled videos as members.

## Writing a study

Create `studies/<name>.py` with:

```python
SLUG = "my-study"            # stable id
TITLE = "Does X predict Y?"
SUMMARY = "One paragraph."
METHODOLOGY = """## Question ..."""   # markdown
PARAMS = {...}               # criteria parameters (frozen when locked)
REQUIRES = ["media@1", "tribe_video@1"]   # analyzer@version; drives completeness and gap filling
MEASURES = [{"name": ..., "definition": ...}]

def criteria(store, params, channel_id=None) -> Criteria: ...
def analyze(store, members, params, out_dir) -> {"results": {...}, "figures": ["fig.png"]}
```

`store` (see `studies/base.py`) gives read-only access to channels, videos, analyzer
results and files, retention curves, heatmaps and captions. `studies/base.py` also has
the shared statistics: paired Wilcoxon, sign-flip permutation, Holm correction, Spearman,
circular-shift nulls and curve resampling. The app picks the module up on its next start.

## The two studies

- **Does TribeV2 predict long-form virality?** (`studies/virality_v1.py`): hit and flop
  pairs within Evan's channels; paired tests on network and engagement measures; brain
  versus cheap-feature versus pre-brain-embedding classifiers.
- **Does TRIBE predict real audience retention?** (`studies/retention_v1.py`): within-video
  correlation of TRIBE engagement curves with residual retention and drop-off, with
  circular-shift nulls, leave-one-video-out ridge against cheap controls, and a
  heatmap-versus-real-retention proxy check. Members are only Evan's own videos with BOTH a
  finished TRIBE result and a real YouTube Analytics retention curve; videos missing one are
  listed as candidates ("needs TRIBE" / "needs retention"), and the public heatmap is a
  secondary comparison, never a stand-in for retention.

Both start as drafts: their measures are proposals until Evan locks them.

## Analyzers the studies use

- `tribe_engagement@1`: per-region curves on Yeo-7 networks (the FreeSurfer fsaverage5
  annotation, fetched once; if it can't be fetched, an approximation from HCP-MMP1 areas,
  recorded in the output) and on HCP-MMP1 areas. It gives stimulus drive, the engagement
  index and the anterior insula. **NAcc is not available**: no subcortical TRIBE v2
  checkpoint has been released (tribev2 only ships the cortical model; its subcortical
  setup is a training grid).
- `visual_features@1`, `audio_features@1`: cheap per-second controls. Speech and music are
  heuristics; speech comes from captions when available.
- `resmem@1`: thumbnail memorability. It needs the runner's optional extra:
  `uv sync --project runners --extra resmem`.
