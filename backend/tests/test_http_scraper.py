"""
Comprehensive tests for Native HTTP-based YouTube Scraper (Phase 1).
Tests channel resolution, metadata extraction, pagination, continuation tokens,
error handling (403, 404, 429, timeouts), retry behavior, and provider factory.
"""
import json
import pytest
from unittest.mock import patch, MagicMock, AsyncMock
import httpx

from app.services.youtube.http_scraper import (
    HttpYouTubeScraper,
    YouTubeNotFoundError,
    YouTubeRateLimitError,
    YouTubeAccessForbiddenError,
    YouTubeContentUnavailableError,
    YouTubeParseError,
    YouTubeScrapingError,
    parse_duration_to_seconds,
    parse_count_number,
    extract_json_blob,
)
from app.services.youtube.metadata_provider import get_youtube_provider, DemoYouTubeProvider


# ─── Sample HTML / JSON Fixtures ──────────────────────────────────────────────

MOCK_CHANNEL_HTML = """
<!DOCTYPE html>
<html>
<head>
  <meta itemprop="channelId" content="UC_SAMPLE_CHANNEL_123">
  <title>Sample Creator - YouTube</title>
</head>
<body>
<script>
var ytInitialData = {
  "metadata": {
    "channelMetadataRenderer": {
      "title": "Tech Innovations",
      "description": "Weekly deep dives into cutting-edge tech.",
      "externalId": "UC_SAMPLE_CHANNEL_123",
      "vanityChannelUrl": "http://www.youtube.com/@techinnovations",
      "avatar": {
        "thumbnails": [
          {"url": "https://yt3.example.com/avatar_100.jpg", "width": 100, "height": 100},
          {"url": "https://yt3.example.com/avatar_800.jpg", "width": 800, "height": 800}
        ]
      }
    }
  },
  "header": {
    "c4TabbedHeaderRenderer": {
      "channelId": "UC_SAMPLE_CHANNEL_123",
      "title": "Tech Innovations",
      "subscriberCountText": {"simpleText": "2.45M subscribers"},
      "videosCountText": {"runs": [{"text": "342 videos"}]}
    }
  },
  "contents": {
    "twoColumnBrowseResultsRenderer": {
      "tabs": [
        {
          "tabRenderer": {
            "title": "Videos",
            "selected": true,
            "content": {
              "richGridRenderer": {
                "contents": [
                  {
                    "richItemRenderer": {
                      "content": {
                        "videoRenderer": {
                          "videoId": "VID_001",
                          "title": {"runs": [{"text": "Quantum Computing Breakthrough 2026"}]},
                          "descriptionSnippet": {"runs": [{"text": "A complete breakdown of quantum advances."}]},
                          "thumbnail": {
                            "thumbnails": [
                              {"url": "https://i.ytimg.com/vi/VID_001/hqdefault.jpg", "width": 480, "height": 360}
                            ]
                          },
                          "lengthText": {"simpleText": "14:25"},
                          "viewCountText": {"simpleText": "1,420,000 views"},
                          "ownerText": {"runs": [{"text": "Tech Innovations"}]}
                        }
                      }
                    }
                  },
                  {
                    "richItemRenderer": {
                      "content": {
                        "videoRenderer": {
                          "videoId": "VID_002",
                          "title": {"runs": [{"text": "Next-Gen AI Hardware Architecture"}]},
                          "thumbnail": {
                            "thumbnails": [
                              {"url": "https://i.ytimg.com/vi/VID_002/hqdefault.jpg", "width": 480, "height": 360}
                            ]
                          },
                          "lengthText": {"simpleText": "8:50"},
                          "viewCountText": {"simpleText": "850K views"},
                          "ownerText": {"runs": [{"text": "Tech Innovations"}]}
                        }
                      }
                    }
                  },
                  {
                    "continuationItemRenderer": {
                      "continuationEndpoint": {
                        "continuationCommand": {
                          "token": "TOKEN_PAGE_2_CONTINUATION"
                        }
                      }
                    }
                  }
                ]
              }
            }
          }
        }
      ]
    }
  }
};
</script>
</body>
</html>
"""

MOCK_VIDEO_WATCH_HTML = """
<!DOCTYPE html>
<html>
<head><title>Watch Video</title></head>
<body>
<script>
var ytInitialPlayerResponse = {
  "playabilityStatus": {
    "status": "OK"
  },
  "videoDetails": {
    "videoId": "Vmo4SakRHnI",
    "title": "M3 Max MacBook Pro Deep Dive",
    "lengthSeconds": "1535",
    "viewCount": "2450000",
    "shortDescription": "Detailed benchmarks and real-world battery tests.",
    "thumbnail": {
      "thumbnails": [
        {"url": "https://i.ytimg.com/vi/Vmo4SakRHnI/maxresdefault.jpg", "width": 1280, "height": 720}
      ]
    },
    "author": "Marques Brownlee",
    "channelId": "UCBJycsmduvYEL83R_U4JriQ"
  }
};
</script>
</body>
</html>
"""

MOCK_EMPTY_CHANNEL_HTML = """
<!DOCTYPE html>
<html>
<head><title>Empty Channel</title></head>
<body>
<script>
var ytInitialData = {
  "metadata": {
    "channelMetadataRenderer": {
      "title": "Empty Creator",
      "externalId": "UC_EMPTY_123"
    }
  },
  "contents": {
    "twoColumnBrowseResultsRenderer": {
      "tabs": [
        {
          "tabRenderer": {
            "title": "Videos",
            "content": {
              "richGridRenderer": {
                "contents": []
              }
            }
          }
        }
      ]
    }
  }
};
</script>
</body>
</html>
"""


# ─── Unit Tests for Helper Parsers ────────────────────────────────────────────

def test_parse_duration_to_seconds():
    assert parse_duration_to_seconds("14:25") == 865
    assert parse_duration_to_seconds("1:02:15") == 3735
    assert parse_duration_to_seconds("45") == 45
    assert parse_duration_to_seconds(None) is None
    assert parse_duration_to_seconds("invalid") is None


def test_parse_count_number():
    assert parse_count_number("18.4M subscribers") == 18_400_000
    assert parse_count_number("1.2K views") == 1_200
    assert parse_count_number("1,420,000 views") == 1_420_000
    assert parse_count_number("450 videos") == 450
    assert parse_count_number("No views") is None
    assert parse_count_number(None) is None


def test_extract_json_blob():
    html = '<html><script>var testObj = {"key": "value", "nested": {"a": [1, 2]}};</script></html>'
    res = extract_json_blob(html, "testObj")
    assert res is not None
    assert res["key"] == "value"
    assert res["nested"]["a"] == [1, 2]

    # Non-existent
    assert extract_json_blob(html, "missingVar") is None


# ─── Scraper Core Tests ───────────────────────────────────────────────────────

@pytest.mark.asyncio
async def test_get_channel_info_valid_html():
    scraper = HttpYouTubeScraper()

    mock_resp = MagicMock()
    mock_resp.status_code = 200
    mock_resp.text = MOCK_CHANNEL_HTML

    with patch.object(scraper, "_send_request_with_retry", AsyncMock(return_value=mock_resp)):
        meta = await scraper.get_channel_info("https://www.youtube.com/@techinnovations")

        assert meta.youtube_channel_id == "UC_SAMPLE_CHANNEL_123"
        assert meta.name == "Tech Innovations"
        assert meta.handle == "@techinnovations"
        assert meta.subscriber_count == 2_450_000
        assert meta.video_count == 342
        assert "avatar_800.jpg" in meta.thumbnail_url
        assert "Weekly deep dives" in meta.description


@pytest.mark.asyncio
async def test_get_channel_info_handle_input():
    scraper = HttpYouTubeScraper()

    mock_resp = MagicMock()
    mock_resp.status_code = 200
    mock_resp.text = MOCK_CHANNEL_HTML

    with patch.object(scraper, "_send_request_with_retry", AsyncMock(return_value=mock_resp)) as mock_send:
        await scraper.get_channel_info("@techinnovations")
        # Verify normalization target
        called_url = mock_send.call_args[0][0]
        assert "youtube.com/@techinnovations/videos" in called_url


@pytest.mark.asyncio
async def test_get_video_info_valid():
    scraper = HttpYouTubeScraper()

    mock_resp = MagicMock()
    mock_resp.status_code = 200
    mock_resp.text = MOCK_VIDEO_WATCH_HTML

    with patch.object(scraper, "_send_request_with_retry", AsyncMock(return_value=mock_resp)):
        vid = await scraper.get_video_info("Vmo4SakRHnI")

        assert vid.youtube_video_id == "Vmo4SakRHnI"
        assert vid.title == "M3 Max MacBook Pro Deep Dive"
        assert vid.duration_seconds == 1535
        assert vid.view_count == 2_450_000
        assert vid.channel_name == "Marques Brownlee"
        assert "maxresdefault.jpg" in vid.thumbnail_url


@pytest.mark.asyncio
async def test_get_channel_info_from_individual_video_url():
    scraper = HttpYouTubeScraper()

    mock_video_resp = MagicMock()
    mock_video_resp.status_code = 200
    mock_video_resp.text = MOCK_VIDEO_WATCH_HTML

    mock_channel_resp = MagicMock()
    mock_channel_resp.status_code = 200
    mock_channel_resp.text = MOCK_CHANNEL_HTML

    with patch.object(scraper, "_send_request_with_retry", AsyncMock(side_effect=[mock_video_resp, mock_channel_resp])):
        meta = await scraper.get_channel_info("https://youtu.be/Vmo4SakRHnI")
        assert meta.youtube_channel_id == "UC_SAMPLE_CHANNEL_123"
        assert meta.name == "Tech Innovations"


@pytest.mark.asyncio
async def test_get_channel_videos_first_page_and_deduplication():
    scraper = HttpYouTubeScraper()

    mock_resp = MagicMock()
    mock_resp.status_code = 200
    mock_resp.text = MOCK_CHANNEL_HTML

    with patch.object(scraper, "_send_request_with_retry", AsyncMock(return_value=mock_resp)):
        videos = await scraper.get_channel_videos("UC_SAMPLE_CHANNEL_123", page=1, page_size=2)

        assert len(videos) == 2
        assert videos[0].youtube_video_id == "VID_001"
        assert videos[0].duration_seconds == 865
        assert videos[0].view_count == 1_420_000
        assert videos[1].youtube_video_id == "VID_002"
        assert videos[1].duration_seconds == 530


@pytest.mark.asyncio
async def test_get_channel_videos_continuation_pagination():
    scraper = HttpYouTubeScraper()

    # Initial page response
    mock_page1_resp = MagicMock()
    mock_page1_resp.status_code = 200
    mock_page1_resp.text = MOCK_CHANNEL_HTML

    # Continuation POST response from /youtubei/v1/browse
    mock_continuation_data = {
        "onResponseReceivedActions": [
            {
                "appendContinuationItemsAction": {
                    "continuationItems": [
                        {
                            "richItemRenderer": {
                                "content": {
                                    "videoRenderer": {
                                        "videoId": "VID_003",
                                        "title": {"runs": [{"text": "Silicon Photonics Revolution"}]},
                                        "lengthText": {"simpleText": "11:20"},
                                        "viewCountText": {"simpleText": "620K views"}
                                    }
                                }
                            }
                        }
                    ]
                }
            }
        ]
    }
    mock_cont_resp = MagicMock()
    mock_cont_resp.status_code = 200
    mock_cont_resp.json.return_value = mock_continuation_data

    with patch.object(scraper, "_send_request_with_retry", AsyncMock(side_effect=[mock_page1_resp, mock_cont_resp])):
        # Request page 2 with page_size=2 (which requires 3 items total to fill page 2)
        videos = await scraper.get_channel_videos("UC_SAMPLE_CHANNEL_123", page=2, page_size=2)

        # Page 2 should contain VID_003
        assert len(videos) == 1
        assert videos[0].youtube_video_id == "VID_003"
        assert videos[0].duration_seconds == 680
        assert videos[0].view_count == 620_000


@pytest.mark.asyncio
async def test_empty_channel_handling():
    scraper = HttpYouTubeScraper()

    mock_resp = MagicMock()
    mock_resp.status_code = 200
    mock_resp.text = MOCK_EMPTY_CHANNEL_HTML

    with patch.object(scraper, "_send_request_with_retry", AsyncMock(return_value=mock_resp)):
        videos = await scraper.get_channel_videos("UC_EMPTY_123", page=1, page_size=24)
        assert videos == []


# ─── Error Handling & Resilience Tests ────────────────────────────────────────

@pytest.mark.asyncio
async def test_error_404_not_found():
    scraper = HttpYouTubeScraper()

    mock_resp = MagicMock()
    mock_resp.status_code = 404

    with patch("httpx.AsyncClient.get", AsyncMock(return_value=mock_resp)):
        with pytest.raises(YouTubeNotFoundError) as exc:
            await scraper.get_channel_info("@non_existent_channel_xyz_404")
        assert "not found" in str(exc.value).lower()


@pytest.mark.asyncio
async def test_error_403_access_forbidden():
    scraper = HttpYouTubeScraper()

    mock_resp = MagicMock()
    mock_resp.status_code = 403

    with patch("httpx.AsyncClient.get", AsyncMock(return_value=mock_resp)):
        with pytest.raises(YouTubeAccessForbiddenError):
            await scraper.get_channel_info("@blocked_channel")


@pytest.mark.asyncio
async def test_error_429_rate_limit():
    scraper = HttpYouTubeScraper()
    scraper.INITIAL_BACKOFF = 0.01  # Fast test

    mock_resp = MagicMock()
    mock_resp.status_code = 429
    mock_resp.headers = {"Retry-After": "0"}

    with patch("httpx.AsyncClient.get", AsyncMock(return_value=mock_resp)):
        with pytest.raises(YouTubeRateLimitError):
            await scraper.get_channel_info("@rate_limited_channel")


@pytest.mark.asyncio
async def test_video_unavailable_restricted():
    scraper = HttpYouTubeScraper()

    unavailable_html = """
    <script>
    var ytInitialPlayerResponse = {
      "playabilityStatus": {
        "status": "UNPLAYABLE",
        "reason": "This video is private."
      }
    };
    </script>
    """
    mock_resp = MagicMock()
    mock_resp.status_code = 200
    mock_resp.text = unavailable_html

    with patch.object(scraper, "_send_request_with_retry", AsyncMock(return_value=mock_resp)):
        with pytest.raises(YouTubeContentUnavailableError) as exc:
            await scraper.get_video_info("PRIVATE_VID")
        assert "private" in str(exc.value).lower()


@pytest.mark.asyncio
async def test_anti_bot_detection_challenge_rejected():
    scraper = HttpYouTubeScraper()

    bot_challenge_html = "<html><body>Our systems have detected unusual traffic from your computer network.</body></html>"
    mock_resp = MagicMock()
    mock_resp.status_code = 200
    mock_resp.text = bot_challenge_html

    with patch.object(scraper, "_send_request_with_retry", AsyncMock(return_value=mock_resp)):
        with pytest.raises(YouTubeAccessForbiddenError) as exc:
            await scraper.get_channel_info("@test_bot")
        assert "anti-bot challenge" in str(exc.value).lower()


@pytest.mark.asyncio
async def test_unexpected_page_structure_parse_error():
    scraper = HttpYouTubeScraper()

    empty_html = "<html><body><h1>Welcome to YouTube</h1></body></html>"
    mock_resp = MagicMock()
    mock_resp.status_code = 200
    mock_resp.text = empty_html

    with patch.object(scraper, "_send_request_with_retry", AsyncMock(return_value=mock_resp)):
        with pytest.raises(YouTubeParseError):
            await scraper.get_channel_info("@invalid_structure")


# ─── Factory Provider Selection Tests ─────────────────────────────────────────

def test_provider_factory_routing():
    # 1. Force HTTP provider
    p_http = get_youtube_provider(force_provider="http")
    assert isinstance(p_http, HttpYouTubeScraper)

    # 2. Demo mode provider
    p_demo = get_youtube_provider(force_provider="demo")
    assert isinstance(p_demo, DemoYouTubeProvider)


# ─── lockupViewModel & Modern YouTube Web Layout Tests ────────────────────────

MOCK_LOCKUP_CHANNEL_HTML = """
<!DOCTYPE html>
<html>
<head>
  <meta itemprop="channelId" content="UC_LOCKUP_CH_999">
  <title>Modern Creator - YouTube</title>
</head>
<body>
<script>
var ytInitialData = {
  "metadata": {
    "channelMetadataRenderer": {
      "title": "Modern Devs",
      "description": "Tutorials and updates.",
      "externalId": "UC_LOCKUP_CH_999",
      "vanityChannelUrl": "http://www.youtube.com/@moderndevs"
    }
  },
  "header": {
    "pageHeaderRenderer": {
      "pageTitle": "Modern Devs",
      "content": {
        "pageHeaderViewModel": {
          "title": {"dynamicTextViewModel": {"text": {"content": "Modern Devs"}}},
          "metadata": {
            "contentMetadataViewModel": {
              "metadataRows": [
                {
                  "metadataParts": [
                    {"text": {"content": "@moderndevs"}},
                    {"text": {"content": "2.69M subscribers"}},
                    {"text": {"content": "6.1K videos"}}
                  ]
                }
              ]
            }
          }
        }
      }
    }
  },
  "contents": {
    "twoColumnBrowseResultsRenderer": {
      "tabs": [
        {
          "tabRenderer": {
            "title": "Videos",
            "selected": true,
            "content": {
              "richGridRenderer": {
                "contents": [
                  {
                    "richItemRenderer": {
                      "content": {
                        "lockupViewModel": {
                          "contentId": "LOCKUP_VID1",
                          "rendererContext": {
                            "commandContext": {
                              "onTap": {
                                "innertubeCommand": {
                                  "watchEndpoint": {"videoId": "LOCKUP_VID1"}
                                }
                              }
                            }
                          },
                          "contentImage": {
                            "thumbnailViewModel": {
                              "image": {
                                "sources": [
                                  {"url": "https://i.ytimg.com/vi/LOCKUP_VID1/hqdefault.jpg", "width": 480, "height": 360}
                                ]
                              },
                              "overlays": [
                                {
                                  "thumbnailBottomOverlayViewModel": {
                                    "badges": [
                                      {"thumbnailBadgeViewModel": {"text": "3:45"}}
                                    ]
                                  }
                                }
                              ]
                            }
                          },
                          "metadata": {
                            "lockupMetadataViewModel": {
                              "title": {"content": "Modern Web Development in 2026"},
                              "metadata": {
                                "contentMetadataViewModel": {
                                  "metadataRows": [
                                    {
                                      "metadataParts": [
                                        {"text": {"content": "34K views"}},
                                        {"text": {"content": "3 days ago"}}
                                      ]
                                    }
                                  ]
                                }
                              }
                            }
                          }
                        }
                      }
                    }
                  }
                ]
              }
            }
          }
        }
      ]
    }
  }
};
</script>
</body>
</html>
"""


@pytest.mark.asyncio
async def test_get_channel_info_and_videos_lockup_view_model():
    scraper = HttpYouTubeScraper()
    mock_resp = MagicMock()
    mock_resp.status_code = 200
    mock_resp.text = MOCK_LOCKUP_CHANNEL_HTML

    with patch.object(scraper, "_send_request_with_retry", AsyncMock(return_value=mock_resp)):
        # 1. Verify channel info parses 6.1K videos and 2.69M subscribers
        ch = await scraper.get_channel_info("@moderndevs")
        assert ch.youtube_channel_id == "UC_LOCKUP_CH_999"
        assert ch.subscriber_count == 2690000
        assert ch.video_count == 6100

        # 2. Verify videos page parses lockupViewModel
        videos = await scraper.get_channel_videos("UC_LOCKUP_CH_999")
        assert len(videos) == 1
        v = videos[0]
        assert v.youtube_video_id == "LOCKUP_VID1"
        assert v.title == "Modern Web Development in 2026"
        assert v.duration_seconds == 225  # 3 * 60 + 45
        assert v.view_count == 34000
        assert "LOCKUP_VID1" in v.thumbnail_url


def test_parse_count_number_word_units():
    assert parse_count_number("34 thousand views") == 34000
    assert parse_count_number("2.69 million subscribers") == 2690000
    assert parse_count_number("1.5 billion views") == 1500000000
    assert parse_count_number("6.1K videos") == 6100
    assert parse_count_number("420 videos") == 420
