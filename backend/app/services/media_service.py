"""视频轻加工（P2）：ffmpeg 取帧 / 截取。derived from 无（新能力）。



约束：

- ffmpeg 以参数列表调用（无 shell），时间/时长为 float，杜绝注入面

- 输入只接受 /outputs/ 本地路径或 http(s) 远端（下载有大小上限）

- 截取优先 -c copy（秒级无损），关键帧不对齐失败时回退 libx264 重编码

"""



from __future__ import annotations

import re
import shutil
import subprocess
import time
import uuid
from pathlib import Path

import httpx

FFMPEG_BIN = "ffmpeg"

FFMPEG_TIMEOUT = 120

MAX_DOWNLOAD_BYTES = 200 * 1024 * 1024

MAX_TRIM_SPAN = 600.0

MIN_TRIM_SPAN = 0.5





class MediaError(Exception):

    """带稳定错误码的媒体加工失败（router 映射为 envelope）。"""



    def __init__(self, code: str, message: str, hint: str = ""):

        self.code = code

        self.message = message

        self.hint = hint

        super().__init__(message)





def _outputs_root_default() -> Path:

    return Path("outputs")





def ensure_local(url: str, outputs_root: Path | None = None) -> Path:

    """输入素材定位：/outputs/ 本地直读（拒绝其他绝对路径防逃逸）；远端流式下载。"""

    root = outputs_root or _outputs_root_default()

    if url.startswith("/outputs/"):

        rel = url[len("/outputs/"):]

        p = (root / rel).resolve()

        if not str(p).startswith(str(root.resolve())):

            raise MediaError("MEDIA_URL_INVALID", "非法素材路径")

        if not p.is_file():

            raise MediaError("MEDIA_NOT_FOUND", "素材不存在或已被清理", "请重新生成后重试")

        return p

    if url.startswith(("http://", "https://")):

        dest_dir = root / "media" / "downloads"

        dest_dir.mkdir(parents=True, exist_ok=True)

        dest = dest_dir / f"dl_{int(time.time())}_{uuid.uuid4().hex[:8]}.mp4"

        try:

            with httpx.stream("GET", url, timeout=60, follow_redirects=True) as resp:

                resp.raise_for_status()

                size = 0

                with dest.open("wb") as fh:

                    for chunk in resp.iter_bytes(1024 * 256):

                        size += len(chunk)

                        if size > MAX_DOWNLOAD_BYTES:

                            raise MediaError(

                                "MEDIA_TOO_LARGE", "视频超过 200MB 上限", "请截短后重试"

                            )

                        fh.write(chunk)

        except MediaError:

            dest.unlink(missing_ok=True)

            raise

        except httpx.HTTPError as e:

            dest.unlink(missing_ok=True)

            raise MediaError("MEDIA_FETCH_FAILED", f"视频下载失败: {type(e).__name__}") from e

        return dest

    raise MediaError("MEDIA_URL_INVALID", "仅支持 /outputs/ 路径或 http(s) 地址")





def _run_ffmpeg(args: list[str], *, strict: bool = True) -> bool:

    try:

        proc = subprocess.run(  # noqa: S603 — 参数列表构造，无 shell

            [FFMPEG_BIN, "-hide_banner", "-loglevel", "error", "-y", *args],

            capture_output=True,

            text=True,

            timeout=FFMPEG_TIMEOUT,

        )

    except FileNotFoundError as e:

        raise MediaError(

            "FFMPEG_MISSING", "服务端未安装 ffmpeg", "请在镜像/系统中安装 ffmpeg 后重试"

        ) from e

    except subprocess.TimeoutExpired as e:

        raise MediaError("FFMPEG_FAILED", "ffmpeg 处理超时", "请缩短截取区间后重试") from e

    if proc.returncode != 0:

        if not strict:

            return False  # 容忍模式（copy 快路径失败由调用方回退重编码）

        raise MediaError(

            "FFMPEG_FAILED",

            f"ffmpeg 失败: {proc.stderr.strip()[-200:] or proc.returncode}",

            "请检查素材格式后重试",

        )

    return True





def _ffprobe_has_audio(path: Path) -> bool:

    """探测视频文件是否含音轨。"""

    try:

        proc = subprocess.run(  # noqa: S603

            ["ffprobe", "-v", "error", "-select_streams", "a",

             "-show_entries", "stream=codec_type", "-of", "csv=p=0", str(path)],

            capture_output=True, text=True, timeout=15,

        )

        return "audio" in proc.stdout

    except Exception:

        return False





def _out_path(root: Path, prefix: str, ext: str) -> Path:

    d = root / "media"

    d.mkdir(parents=True, exist_ok=True)

    return d / f"{prefix}_{int(time.time() * 1000)}_{uuid.uuid4().hex[:10]}{ext}"





def extract_frame(video: Path, t: float, outputs_root: Path | None = None) -> Path:

    """提取指定时间点单帧为 PNG（input seeking，快）。"""

    root = outputs_root or _outputs_root_default()

    if t < 0:

        raise MediaError("MEDIA_PARAMS_INVALID", "时间点不能为负")

    out = _out_path(root, "frame", ".png")

    _run_ffmpeg(["-ss", f"{t:.3f}", "-i", str(video), "-frames:v", "1", "-q:v", "2", str(out)])

    if not out.is_file() or out.stat().st_size == 0:

        raise MediaError("FFMPEG_FAILED", "未能提取帧（时间点可能超出时长）", "请调整时间点")

    return out





def extract_last_frame(video: Path, outputs_root: Path | None = None) -> Path:

    """提取末帧为 PNG（-sseof 尾部定位，快）。分镜尾帧接力的地基。"""

    root = outputs_root or _outputs_root_default()

    out = _out_path(root, "frame", ".png")

    _run_ffmpeg(["-sseof", "-1.0", "-i", str(video), "-frames:v", "1", "-q:v", "2", str(out)])

    if not out.is_file() or out.stat().st_size == 0:

        raise MediaError("FFMPEG_FAILED", "未能提取末帧", "请检查视频文件")

    return out





def trim(video: Path, start: float, end: float, outputs_root: Path | None = None) -> Path:

    """截取 [start, end)：先 -c copy，产物异常时回退 libx264。"""

    root = outputs_root or _outputs_root_default()

    if start < 0 or end <= start:

        raise MediaError("MEDIA_PARAMS_INVALID", "截取区间无效（需 0 ≤ start < end）")

    span = end - start

    if span < MIN_TRIM_SPAN or span > MAX_TRIM_SPAN:

        raise MediaError(

            "MEDIA_PARAMS_INVALID", f"截取跨度需在 {MIN_TRIM_SPAN}~{MAX_TRIM_SPAN:.0f} 秒"

        )



    out = _out_path(root, "trim", ".mp4")

    # -ss 前置时输出时间轴重置，用 -t 携带跨度更稳；copy 失败（关键帧不对齐等）容忍

    _run_ffmpeg(

        ["-ss", f"{start:.3f}", "-i", str(video), "-t", f"{span:.3f}", "-c", "copy", str(out)],

        strict=False,

    )

    if not out.is_file() or out.stat().st_size <= 1024:

        # 关键帧不对齐等 copy 失败：重编码兜底

        out.unlink(missing_ok=True)

        _run_ffmpeg(

            [

                "-ss", f"{start:.3f}", "-i", str(video), "-t", f"{span:.3f}",

                "-c:v", "libx264", "-preset", "veryfast", "-c:a", "aac", str(out),

            ]

        )

    if not out.is_file() or out.stat().st_size == 0:

        raise MediaError("FFMPEG_FAILED", "截取产物生成失败", "请调整区间后重试")

    return out





# ---------- P2-8: 字幕烧录 / 视频放大 ----------



MIN_ZOOM = 1.2

MAX_ZOOM = 4.0





def _ffprobe_dims(video: Path) -> tuple[int, int]:

    """ffprobe 读取视频宽高（zoom 计算裁剪窗口）。"""

    import json as _json



    try:

        args = [

            "ffprobe", "-hide_banner", "-v", "error",

            "-select_streams", "v:0", "-show_entries",

            "stream=width,height", "-of", "json", str(video),

        ]

        proc = subprocess.run(args, capture_output=True, text=True, timeout=30)  # noqa: S603

        streams = _json.loads(proc.stdout or "{}").get("streams", [])

        w, h = int(streams[0]["width"]), int(streams[0]["height"])

        if w > 0 and h > 0:

            return w, h

    except (FileNotFoundError, KeyError, ValueError, subprocess.TimeoutExpired, _json.JSONDecodeError):  # noqa: E501

        pass

    raise MediaError("FFPROBE_FAILED", "无法读取视频信息", "素材可能损坏，请重新生成")





_SRT_TIME_RE = re.compile(r"\d{1,2}:\d{2}:\d{2}[,.]\d{3}\s*-->")





def burn_subtitles(video: Path, text: str, outputs_root: Path | None = None) -> Path:

    """烧录字幕：srt 格式直用；纯文本自动生成全片单条。



    烧录必须重编码（copy 无法挂滤镜），字幕路径需转义冒号（ffmpeg 滤镜语法）。

    """



    root = outputs_root or _outputs_root_default()

    text = (text or "").strip()

    if not text:

        raise MediaError("MEDIA_PARAMS_INVALID", "字幕内容不能为空")

    if len(text) > 10_000:

        raise MediaError("MEDIA_PARAMS_INVALID", "字幕过长（≤10000 字符）")



    media_dir = root / "media"

    media_dir.mkdir(parents=True, exist_ok=True)

    srt_path = _out_path(root, "subs", ".srt")

    if _SRT_TIME_RE.search(text):

        srt_path.write_text(text + "\n", encoding="utf-8")

    else:

        srt_path.write_text(f"1\n00:00:00,000 --> 00:09:59,000\n{text}\n", encoding="utf-8")



    out = _out_path(root, "subs_burn", ".mp4")

    # srt 路径在滤镜里冒号要转义；单引号包一层防空格

    esc = str(srt_path.resolve()).replace("\\", "/").replace(":", "\\:")

    _run_ffmpeg([

        "-i", str(video),

        "-vf", f"subtitles='{esc}'",

        "-c:a", "copy",

        str(out),

    ])

    if not out.is_file() or out.stat().st_size == 0:

        raise MediaError("FFMPEG_FAILED", "字幕烧录失败", "请检查字幕格式（支持 srt 或纯文本）")

    return out





def zoom(video: Path, factor: float, outputs_root: Path | None = None) -> Path:

    """视频放大：中心裁剪 1/factor 窗口再放回原分辨率（视觉推近）。"""

    root = outputs_root or _outputs_root_default()

    if not (MIN_ZOOM <= factor <= MAX_ZOOM):

        raise MediaError(

            "MEDIA_PARAMS_INVALID", f"放大倍数需在 {MIN_ZOOM}~{MAX_ZOOM}"

        )

    w, h = _ffprobe_dims(video)

    cw = max(w // round(factor), 2)

    ch = max(h // round(factor), 2)

    out = _out_path(root, "zoom", ".mp4")

    _run_ffmpeg([

        "-i", str(video),

        "-vf", f"crop={cw}:{ch}:(iw-ow)/2:(ih-oh)/2,scale={w}:{h}",

        "-c:a", "copy",

        str(out),

    ])

    if not out.is_file() or out.stat().st_size == 0:

        raise MediaError("FFMPEG_FAILED", "视频放大失败")

    return out





# ---- 成片合成（0.8.0-M3）：归一化 → concat → 可选 BGM ----



_COMPOSE_RESOLUTIONS = {"480p": (854, 480), "720p": (1280, 720), "1080p": (1920, 1080)}

_COMPOSE_TMP_PREFIX = "compose_tmp_"





def _ffprobe_duration(video: Path) -> float:

    """ffprobe 读取时长（秒）；失败回退 6.0（分段时长缺省，不阻断合成）。"""

    import json as _json



    try:

        args = [

            "ffprobe", "-hide_banner", "-v", "error",

            "-show_entries", "format=duration", "-of", "json", str(video),

        ]

        proc = subprocess.run(args, capture_output=True, text=True, timeout=30)  # noqa: S603

        return float((_json.loads(proc.stdout or "{}").get("format") or {}).get("duration") or 6.0)

    except (FileNotFoundError, ValueError, subprocess.TimeoutExpired, _json.JSONDecodeError):

        return 6.0





def compose_video(

    segment_urls: list[str],

    outputs_root: Path,

    *,

    resolution: str = "480p",

    bgm_url: str | None = None,

    bgm_volume: float = 0.25,

) -> Path:

    """completed 段按序合成成片：每段归一化（统一画布/30fps/补静音轨）→ concat → 可选 BGM。



    混合引擎段参数不一致（分辨率/帧率/有无音轨），必须逐段独立转码后再 concat（Review Focus #2）。

    """

    if resolution not in _COMPOSE_RESOLUTIONS:

        raise MediaError(

            "MEDIA_PARAMS_INVALID",

            f"不支持的分辨率: {resolution}",

            "支持 480p / 720p / 1080p",

        )

    if not segment_urls:

        raise MediaError("MEDIA_PARAMS_INVALID", "没有可合成的分段")

    w, h = _COMPOSE_RESOLUTIONS[resolution]



    work = outputs_root / "videos"

    work.mkdir(parents=True, exist_ok=True)

    stamp = int(time.time() * 1000)

    tmp_dir = work / f"{_COMPOSE_TMP_PREFIX}{stamp}"

    tmp_dir.mkdir()

    out_path = work / f"compose_{stamp}.mp4"



    def _out(p: Path) -> Path:

        if not p.is_file() or p.stat().st_size == 0:

            raise MediaError("FFMPEG_FAILED", "合成产物生成失败", "请检查分段视频后重试")

        return p



    try:

        # 阶段1：逐段归一化

        norm_paths: list[Path] = []

        for i, url in enumerate(segment_urls, start=1):

            seg = ensure_local(url, outputs_root)

            dur = _ffprobe_duration(seg)

            norm = tmp_dir / f"norm_{i}.mp4"

            # 保留原素材音轨: 有音轨映射 0:a, 无音轨回退 anullsrc
            # (V6 fix: 原先无条件静音丢失 H3 生成声)

            has_audio = _ffprobe_has_audio(seg)

            if has_audio:

                _run_ffmpeg(

                    [

                        "-i", str(seg),

                        "-vf",

                        f"scale={w}:{h}:force_original_aspect_ratio=decrease,"

                        f"pad={w}:{h}:(ow-iw)/2:(oh-ih)/2,fps=30,setsar=1",

                        "-shortest",

                        "-map", "0:v:0", "-map", "0:a:0?",

                        "-c:v", "libx264", "-preset", "veryfast", "-crf", "20",

                        "-pix_fmt", "yuv420p", "-c:a", "aac", "-b:a", "128k",

                        str(norm),

                    ]

                )

            else:

                _run_ffmpeg(

                    [

                        "-i", str(seg),

                        "-f", "lavfi", "-t", f"{max(dur, 0.5):.3f}",

                        "-i", "anullsrc=r=44100:cl=stereo",

                        "-vf",

                        f"scale={w}:{h}:force_original_aspect_ratio=decrease,"

                        f"pad={w}:{h}:(ow-iw)/2:(oh-ih)/2,fps=30,setsar=1",

                        "-shortest",

                        "-map", "0:v:0", "-map", "1:a",

                        "-c:v", "libx264", "-preset", "veryfast", "-crf", "20",

                        "-pix_fmt", "yuv420p", "-c:a", "aac",

                        str(norm),

                    ]

                )

            norm_paths.append(_out(norm))



        # 阶段2：concat demuxer 拼接

        list_file = tmp_dir / "list.txt"

        # 必须绝对路径：concat 相对列表文件所在目录解析相对路径，会嵌套失效

        list_file.write_text(

            "".join(f"file '{p.resolve().as_posix()}'\n" for p in norm_paths), encoding="utf-8"

        )

        concat_path = tmp_dir / "concat.mp4"

        _run_ffmpeg(

            ["-f", "concat", "-safe", "0", "-i", str(list_file), "-c", "copy", str(concat_path)]

        )

        _out(concat_path)



        # 阶段3：可选 BGM（音量固定 duck 混音；无 BGM 时 concat 产物即成片）

        if bgm_url:

            bgm = ensure_local(bgm_url, outputs_root)

            _run_ffmpeg(

                [

                    "-i", str(concat_path), "-i", str(bgm),

                    "-filter_complex",

                    f"[1:a]volume={bgm_volume}[b];[0:a][b]amix=inputs=2:duration=first[a]",

                    "-map", "0:v", "-map", "[a]",

                    "-c:v", "copy", "-c:a", "aac",

                    str(out_path),

                ]

            )

            _out(out_path)

        else:

            concat_path.replace(out_path)



        return out_path

    finally:

        shutil.rmtree(tmp_dir, ignore_errors=True)





def cleanup_media_downloads(

    outputs_root: Path | None = None, *, max_age_days: int = 7

) -> int:

    """清理 outputs/media/downloads 过期暂存（审计批次二 P2）。



    该目录是 ensure_local 远端素材的临时落点（合成可随时重新下载），

    长期无人清理必然膨胀。返回删除文件数。

    """

    import time as _time



    root = outputs_root or _outputs_root_default()

    dl = root / "media" / "downloads"

    if not dl.is_dir():

        return 0

    cutoff = _time.time() - max_age_days * 86400

    removed = 0

    for p in dl.iterdir():

        try:

            if p.is_file() and p.stat().st_mtime < cutoff:

                p.unlink(missing_ok=True)

                removed += 1

        except OSError:

            continue

    return removed





# 段间转场（用户设计 2026-10-10：合成卡要能选过渡方式）——xfade 白名单

COMPOSE_TRANSITIONS = ("none", "fade", "slideleft", "circleopen", "wipeleft")

_XFADE_DUR = 0.5  # 转场时长（秒）





def compose_videos(

    urls: list[str],

    bgm_url: str | None = None,

    outputs_root: Path | None = None,

    transition: str = "none",

) -> Path:

    """多视频按序拼接成片（可选 BGM 25% 混音；可选段间转场）——画布人工与 Agent 共用。



    归一策略：以首段分辨率为基准，其余段 scale+pad（保持比例黑边）——

    自由创作场景各段可能来自不同渠道/分辨率，直接 concat 会花屏。

    BGM：循环铺满 + 0.25 音量（与分镜成片管线同语义）。

    transition：none=硬切 concat；其余为 xfade 预设（offset 按段时长累计，转场重叠 0.5s）。

    """

    if len(urls) < 2:

        raise MediaError("MEDIA_COMPOSE_INVALID", "合成至少需要 2 段视频", "")

    if transition not in COMPOSE_TRANSITIONS:

        raise MediaError(

            "MEDIA_COMPOSE_TRANSITION_INVALID",

            f"转场须为 {'/'.join(COMPOSE_TRANSITIONS)} 之一",

            "",

        )

    root = outputs_root or _outputs_root_default()

    vids = [ensure_local(u, root) for u in urls]



    w, h = _ffprobe_dims(vids[0])

    inputs: list[str] = []

    filters: list[str] = []

    # 音轨探测: 有轨段映射原音轨, 无轨段补静音源 (V6 fix: 原先 v=1:a=0 丢弃 H3 生成声)

    seg_has_audio = [_ffprobe_has_audio(v) for v in vids]

    extra_inputs: list[str] = []

    extra_count = 0

    a_labels: list[str] = []

    for i, v in enumerate(vids):

        inputs += ["-i", str(v)]

        norm = (

            f"[{i}:v]scale={w}:{h}:force_original_aspect_ratio=decrease,"

            f"pad={w}:{h}:(ow-iw)/2:(oh-ih)/2,setsar=1"

        )

        if transition != "none":

            norm += ",fps=30"  # xfade 需各段同帧率

        filters.append(norm + f"[v{i}]")

        if seg_has_audio[i]:

            filters.append(f"[{i}:a]anull[a{i}]")

            a_labels.append(f"[a{i}]")

        else:

            ai = len(vids) + extra_count

            extra_count += 1

            extra_inputs += ["-f", "lavfi", "-t", "1", "-i", "anullsrc=r=44100:cl=stereo"]

            filters.append(f"[{ai}:a]anull[a{i}]")

            a_labels.append(f"[a{i}]")

    n = len(vids)

    if transition == "none":

        chain = "".join(f"[v{i}]" for i in range(n)) + f"concat=n={n}:v=1:a=0[vout]"

        achain = "".join(a_labels) + f"concat=n={n}:v=0:a=1[aout]"

        fc = ";".join(filters + [chain, achain])

    else:

        durs = [_ffprobe_duration(v) for v in vids]

        prev = "v0"

        acc = durs[0]

        for i in range(1, n):

            offset = max(acc - _XFADE_DUR, 0.0)

            label = f"x{i}"

            filters.append(

                f"[{prev}][v{i}]xfade=transition={transition}:"

                f"duration={_XFADE_DUR}:offset={offset:.3f}[{label}]"

            )

            acc = acc + durs[i] - _XFADE_DUR

            prev = label

        achain = "".join(a_labels) + f"concat=n={n}:v=0:a=1[aout]"

        fc = ";".join(filters) + f";[{prev}]null[vout];" + achain

    out = _out_path(root, "compose", ".mp4")



    maps = ["-map", "[vout]", "-map", "[aout]"]

    audio_args: list[str] = ["-c:a", "aac", "-b:a", "128k", "-shortest"]

    if bgm_url:

        bgm = ensure_local(bgm_url, root)

        inputs += ["-stream_loop", "-1", "-i", str(bgm)]

        fc += f";[{n}:a]volume=0.25[bg]"

        maps += ["-map", "[bg]"]

        audio_args = ["-c:a", "aac", "-shortest"]



    args = (

        inputs

        + extra_inputs

        + ["-filter_complex", fc]

        + maps

        + ["-c:v", "libx264", "-preset", "fast", "-crf", "23"]

        + audio_args

        + ["-y", str(out)]

    )

    if not _run_ffmpeg(args):

        raise MediaError("MEDIA_COMPOSE_FAILED", "拼接执行失败（ffmpeg 日志）", "")

    return out
