# 前人做过的工具（GitHub 调研）

调研目的：导入 iPod 曲库、维护封面和滚动歌词，有没有现成的轮子可以不用自己写。

搜索方式：`gh search repos` + GraphQL `search(type: REPOSITORY)` + `gh search code`。

---

## 1. 结论先行

**导入和封面：有成熟轮子，但都不解决"iPod 曲库 → Apple Music library"这一跳。**
`libgpod` 是解析 iPod 数据库的事实标准，iOpenPod 是完整的 iPod 管理器，
但它们的目标是**往 iPod 里写**，不是**从 iPod 读出来进 Music.app**。

**滚动歌词：本地曲目做不到，这是平台限制不是没人做。**
见 [LYRICS.md](LYRICS.md)：Music.app 只渲染自己云端 TTML 缓存里的逐字歌词，要求曲目带
Apple catalog ID。CD 抓轨的歌没有 ID，所以任何工具都注不进去——LyricFever 那
一类只能在**别人的播放器里**叠一层显示，不写进你的库。

**纯文本歌词：有可直接复用的做法。** `einhund/apple-music-local-lyrics`
用 JXA 的 `track.lyrics = ...` 写入，和 `musiclib/musicapp.py` 的 `set_lyrics()` 是同一机制。

## 2. iTunesDB 解析

| 仓库 | 星 | 语言 | 说明 |
|---|---|---|---|
| `fadingred/libgpod` | — | C | 事实标准。`src/itdb.h` 是字段定义源头 |
| `strawberrymusicplayer/strawberry-libgpod` | — | C | 同上的维护分支 |
| `TheRealSavi/iOpenPod` | 542 | — | 完整 iPod 管理器 + iTunes 替代品，三平台 |
| `raleighlittles/iTunesDB-Parser` | 44 | Rust | 含 `itunesdb_docs/README.md` 格式文档 |
| `ishiichan-dayo/ZenPod` | 43 | — | iPod Classic 管理（iPodOS + Rockbox） |
| `pmerlin1/py-itunesdb-parser` | 0 | Python | 提取曲库和歌单，最接近 `musiclib/itunesdb.py` |
| `dstaley/clickwheel` | — | — | `docs/database/iTunesDB/trackitem.md`，字段文档比 `musiclib/itunesdb.py` 更全 |

`musiclib/itunesdb.py` 的偏移标定可以和 `clickwheel` 的文档交叉验证。

## 3. 歌词写入 iTunes / Music

| 仓库 | 星 | 说明 |
|---|---|---|
| `einhund/apple-music-local-lyrics` | 8 | **JXA 的 `track.lyrics = ...`**，从 Apple Music / Genius 抓词。GPL-3.0 |
| `Wedeniwski/iTunes-Music-AppleScripts` | 0 | 2017 年老脚本，找歌词/封面/信息 |
| `LyricStripper` / `iLyricMP3` | — | 处理 `.lrc` 文件本身，不碰 iTunes |

`einhund` 那个值得看的地方是它做了**来源优先级**和**跳过已有歌词**的逻辑，
和 `lyrics.enrich(only_missing=True)` 的思路一致。它写入的是 Apple Music 网页抓的纯文本，
`musiclib/lyrics.py` 用 LRCLib（有 API、可结构化匹配、时长可校验）。

## 4. 滚动 / 逐字歌词（读侧）

| 仓库 | 星 | 说明 |
|---|---|---|
| `amll-dev/applemusic-like-lyrics` | 2213 | Apple Music 风格歌词**播放组件**，React/Vue |
| `aviwad/LyricFever` | 645 | macOS 菜单栏卡拉OK，MIT，LyricsX 精神续作 |
| `Yudaotor/lyrimuse` | 470 | 逐字歌词，支持 Apple Music/Spotify/YouTube |
| `amll-dev/amll-ttml-db` | 493 | **16139 个 TTML 逐字歌词**，CC0，可当日语料 |
| `amll-dev/amll-ttml-tool` | 412 | 逐字歌词编辑器 |
| `Takpap/apple-music-lyrics` | 17 | 直接读 `~/Library/Caches/com.apple.Music/` 的 TTML 缓存 |
| `mytrashcan/applemusic-menubar-lyrics` | 2 | 同上思路 |

### TTML 缓存的位置（`Takpap` 源码实测）

```
~/Library/Caches/com.apple.Music/Cache.db          ← SQLite 索引
~/Library/Caches/com.apple.Music/fsCachedData/     ← TTML 正文
```

歌曲的 `ttmlLocalizations` 来自 Apple Music API 的
`syllableLyrics.attributes`。**只有走过云端匹配的曲目才有**，本地文件没有。

## 5. 歌词来源

| 来源 | 说明 |
|---|---|
| **LRCLib** (`lrclib.net`) | 免费、无 key、有同步 LRC、返回时长可校验。`musiclib/lyrics.py` 采用 |
| Genius | 纯文本为主，需抓 HTML |
| Apple Music | 网页版歌词，`einhund` 用的就是这条路 |

LRCLib 客户端：`Dr-Blank/lrclibapi`（Python）、`notigorwastaken/lrclib-api`（JS）。
`musiclib/lyrics.py` 只用 REST，没引第三方库。

`lutyjj/lrc-sync`（Rust）是同思路的成熟实现，但它写 `.lrc` sidecar 给通用播放器
用——对 Music.app 无效（见 [LYRICS.md](LYRICS.md)）。

## 6. 逐字歌词格式转换

| 仓库 | 星 | 说明 |
|---|---|---|
| `6hdj/lrc-ttml` | 24 | TXT/LRC + 音频 → Apple Music / AMLL 逐字 TTML |
| `quinn0823/ttml2lrc` | 0 | Apple Music TTML → LRC |

如果以后想给这批歌做逐字歌词，路径是：LRCLib 拿带时间轴的 LRC →
`lrc-ttml` 转 TTML → 但**仍然写不进 Music.app 的本地曲目**，只能喂给 AMLL 播放器。

## 7. 封面

`musiclib/convert.py` 的做法（ffmpeg 内嵌 `covr` + `-disposition:v attached_pic`）就是标准做法，
没找到更好的替代。`h20ray/spotify-apple-music-toolkit` 做类似的事（给本地音频
打标 + 修高分辨率封面），可以参考它处理高清封面的部分。
