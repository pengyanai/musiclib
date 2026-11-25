# 歌词：能到什么程度，为什么

> **Last updated**: 2026-11-25
>
> 结论适用于 macOS Music.app 与 CD 抓轨的本地曲目。

## 结论

**只有纯文本歌词能写进本地曲目。** 逐字、滚动、卡拉OK 效果都不行，
这是平台限制，不是实现难度问题。

## 四种写入路径的结果

| 写入位置 | 结果 | 验证方式 |
|---|---|---|
| `©lyr` atom + 纯文本 | ✅ 显示 | 写入后回读，与原文字字一致 |
| `©lyr` atom + LRC 时间戳 | ❌ 当普通文字 | 重启 Music 后播放，`[00:58.40]` 原样显示，无高亮无滚动 |
| `©lyr` atom + TTML | ❌ 原样返回 | `lyrics` 属性返回整段 XML 字符串，未转成时间轴 |
| 同名 `.lrc` sidecar | ❌ 完全不读 | 写入 → update → 重启，`lyrics` 仍为空字符串 |

## 为什么做不到

滚动歌词在 Music.app 里**只来自它自己缓存的 TTML**：

```
~/Library/Caches/com.apple.Music/Cache.db       ← 索引
~/Library/Caches/com.apple.Music/fsCachedData/  ← TTML 正文
```

这些 TTML 由 Apple Music API 下发，而 API 只对**带 Apple catalog ID 的曲目**
返回 `syllableLyrics`。CD 抓轨的歌没有 catalog ID，因此：

- 本地放 TTML 不会被当成时间轴；
- 放 LRC 不会被解析；
- 放 sidecar 干脆不被读。

三条路都堵死，且都不报错——这正是需要在 SDK 里显式抛错的原因。

## 那还能做什么

### 1. 纯文本歌词（已实现）

`lyrics.enrich()` 从 [LRCLib](https://lrclib.net) 取词。选它是因为：

- 免费、无需 key；
- 返回时长，可用来校验是不是同一版录音；
- 有结构化字段，不比抓 HTML 脆弱。

### 2. 匹配比抓取难

一个中文现场版曲库，同名曲很多。只按歌名搜，返回的往往是录音室版。
两道防线：

- **查询分级**：先精确（`track_name` + `artist_name`），不中再剥括号后缀，最后才退到 `q` 全文搜索；
- **时长窗口**：候选与本地曲目时长差超过 `max_delta`（默认 8 秒）就丢弃。现场版与录音室版差远不止 8 秒，这是最便宜也最有效的判别信号。

### 3. 写入要过临时文件

歌词正文含引号和换行，直接拼进 AppleScript 字符串字面量会截断。
`musicapp.set_lyrics()` 先写临时文件，用 `read (POSIX file ...)` 读入。

## 如果一定要滚动歌词

唯一的路是换播放器，不是换格式：

- `amll-dev/applemusic-like-lyrics`（2213★）— Apple Music 风格歌词播放组件；
- `aviwad/LyricFever`（645★）— macOS 菜单栏卡拉OK，MIT；
- `amll-dev/amll-ttml-db`（493★）— 16139 个 TTML 逐字歌词，CC0。

它们在你的播放器里叠一层显示，**不写进 Music.app 的库**。

另一条理论路径：LRCLib 拿带时间轴的 LRC → `6hdj/lrc-ttml` 转 TTML。
但转出来仍然写不进 Music.app 的本地曲目，只能喂给上面的播放器。

## 对应代码

| 文件 | 内容 |
|---|---|
| `musiclib/lyrics.py` | 取词、分级查询、时长校验、幂等写入 |
| `musiclib/musicapp.py:set_lyrics` | 临时文件写入 |
| `musiclib/capabilities.py` | `lyrics_timed` 在动手前抛 `Unsupported` |
