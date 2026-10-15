import { Link, useNavigate } from "@tanstack/react-router";
import { Search } from "lucide-react";
import { useState } from "react";
import { useAddChannel, useChannels } from "@/api/channels";
import { useChannelSearch } from "@/api/discover";
import { Button } from "@/components/ui/button";
import {
  Alert,
  Badge,
  Card,
  CardContent,
  CardHeader,
  CardTitle,
  Input,
  PageHeader,
  Spinner,
} from "@/components/ui/primitives";
import { fmtAgo, fmtCount } from "@/lib/format";

function looksLikeDirect(q: string) {
  return /^(@|https?:\/\/|www\.|youtube\.com|UC[\w-]{22}$)/.test(q.trim());
}

/** Step 1 of Discover: find a channel (search by name, or paste a URL / @handle / id). */
export function DiscoverPage() {
  const [input, setInput] = useState("");
  const [query, setQuery] = useState("");
  const search = useChannelSearch(query);
  const open = useAddChannel();
  const known = useChannels();
  const navigate = useNavigate();

  const openChannel = (channel: string) =>
    open.mutate(
      { channel },
      {
        onSuccess: (data) =>
          navigate({ to: "/discover/$channelId", params: { channelId: String(data.channel.id) } }),
      },
    );

  const direct = looksLikeDirect(input);

  return (
    <div>
      <PageHeader
        title="Discover"
        description="Find a channel, browse all its uploads, and pull the videos you want to analyze. Browsing never downloads anything."
      />
      <Card className="mb-6">
        <CardContent className="p-4">
          <form
            className="flex flex-wrap gap-3"
            onSubmit={(e) => {
              e.preventDefault();
              const q = input.trim();
              if (!q) return;
              if (direct) openChannel(q);
              else setQuery(q);
            }}
          >
            <Input
              className="min-w-72 flex-1"
              placeholder="Channel name, URL, @handle or UC… id"
              value={input}
              onChange={(e) => setInput(e.target.value)}
              autoFocus
            />
            <Button type="submit" disabled={!input.trim() || open.isPending || search.isFetching}>
              <Search /> {direct ? "Open channel" : "Search"}
            </Button>
          </form>
          <p className="mt-2 text-xs text-muted-foreground">
            {direct
              ? "Opening by URL, handle or id costs about 2 quota units."
              : "A name search uses YouTube's search, which costs 100 of the 10,000 daily quota units."}
          </p>
          {open.error ? <Alert tone="destructive" className="mt-3">{open.error.message}</Alert> : null}
          {search.error ? <Alert tone="destructive" className="mt-3">{search.error.message}</Alert> : null}
        </CardContent>
      </Card>

      {search.isFetching || open.isPending ? <Spinner /> : null}
      {search.data ? (
        <div className="mb-8 grid gap-3 md:grid-cols-2 xl:grid-cols-3">
          {search.data.channels.length === 0 ? <p className="text-muted-foreground">No channels found.</p> : null}
          {search.data.channels.map((c) => (
            <Card key={c.external_id}>
              <CardContent className="flex items-start gap-3 p-4">
                {c.thumbnail_url ? (
                  <img src={c.thumbnail_url} alt="" className="size-12 rounded-full" referrerPolicy="no-referrer" />
                ) : (
                  <div className="size-12 rounded-full bg-muted" />
                )}
                <div className="min-w-0 flex-1">
                  <div className="truncate font-medium">{c.title}</div>
                  <div className="text-xs text-muted-foreground">{c.handle}</div>
                  <div className="mt-1 text-sm tabular">
                    {fmtCount(c.subscribers)} subscribers · {fmtCount(c.video_count)} videos
                  </div>
                  <p className="mt-1 line-clamp-2 text-xs text-muted-foreground">{c.description}</p>
                </div>
                <Button size="sm" onClick={() => openChannel(c.external_id)} disabled={open.isPending}>
                  Open
                </Button>
              </CardContent>
            </Card>
          ))}
        </div>
      ) : null}

      {known.data && known.data.channels.length > 0 ? (
        <Card>
          <CardHeader>
            <CardTitle className="text-base">Channels you've opened</CardTitle>
          </CardHeader>
          <CardContent className="flex flex-wrap gap-2">
            {known.data.channels.map((c) => (
              <Link
                key={c.id}
                to="/discover/$channelId"
                params={{ channelId: String(c.id) }}
                className="flex items-center gap-2 rounded-lg border px-3 py-2 text-sm hover:bg-accent"
              >
                {c.thumbnail_url ? (
                  <img src={c.thumbnail_url} alt="" className="size-6 rounded-full" referrerPolicy="no-referrer" />
                ) : null}
                <span>{c.title}</span>
                {c.tracked_count ? <Badge tone="primary">{c.tracked_count} pulled</Badge> : null}
                <span className="text-xs text-muted-foreground">listed {fmtAgo(c.backfilled_at)}</span>
              </Link>
            ))}
          </CardContent>
        </Card>
      ) : null}
    </div>
  );
}
