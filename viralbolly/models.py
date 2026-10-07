from __future__ import annotations

from dataclasses import asdict, dataclass, field


@dataclass
class Comment:
    author: str
    body: str
    score: int


@dataclass
class Post:
    id: str
    title: str
    author: str
    subreddit: str
    permalink: str
    url: str
    score: int
    num_comments: int
    upvote_ratio: float
    created_utc: float
    selftext: str = ""
    flair: str | None = None
    over_18: bool = False
    kind: str = "text"            # "video", "text" or "news"
    platform: str = "reddit"      # "reddit", "youtube" or "news"
    views: int = 0
    video_url: str | None = None  # what yt-dlp should download
    image_url: str | None = None  # shown inside the screenshot card
    local_video: str | None = None  # offline testing: use this file instead of downloading
    source: str | None = None       # who uploaded the clip on its original site (credited on screen)
    comments: list[Comment] = field(default_factory=list)
    virality: float = 0.0
    interest: float = 0.0
    ref_clip: bool = False          # news with no video of its own: a clip of the same person, played muted

    @property
    def handle(self) -> str:
        if self.platform != "reddit":
            return self.author  # channel or outlet name
        return f"u/{self.author}" if self.author != "[deleted]" else "[deleted]"

    def to_dict(self) -> dict:
        return asdict(self)

    @classmethod
    def from_dict(cls, d: dict) -> "Post":
        d = dict(d)
        d["comments"] = [Comment(**c) for c in d.get("comments", [])]
        return cls(**d)
