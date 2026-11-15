"""视频轻加工（P2）：ffmpeg 取帧/截取 服务契约（mock subprocess，不依赖真实 ffmpeg）。"""

from pathlib import Path
from unittest.mock import patch

import pytest

from backend.app.services.media_service import (
    MediaError,
    compose_video,
    ensure_local,
    extract_frame,
    extract_last_frame,
    trim,
)


def _mk_video(root: Path, rel: str = "videos/a.mp4") -> Path:
    p = root / rel
    p.parent.mkdir(parents=True, exist_ok=True)
    p.write_bytes(b"FAKE_MP4")
    return p


def _capture_run(out_factory):
    """mock subprocess.run：按调用顺序用 out_factory 产出产物文件，返回调用参数列表。"""
    calls: list[list[str]] = []

    def fake_run(args, **kwargs):  # noqa: ANN001, ANN202
        calls.append(args)
        rc = kwargs.get("returncode", 0)
        out_path = Path(args[-1])
        if rc == 0:
            out_factory(out_path)
        class R:
            returncode = rc
            stderr = "fake stderr"
            stdout = '{"streams":[{"width":1920,"height":1080}]}'
        return R()

    return calls, fake_run


class TestEnsureLocal:
    def test_local_outputs_path(self, tmp_path):
        _mk_video(tmp_path)
        p = ensure_local("/outputs/videos/a.mp4", tmp_path)
        assert p == tmp_path / "videos" / "a.mp4"

    def test_local_missing_raises(self, tmp_path):
        with pytest.raises(MediaError) as e:
            ensure_local("/outputs/videos/nope.mp4", tmp_path)
        assert e.value.code == "MEDIA_NOT_FOUND"

    def test_rejects_non_outputs_absolute(self, tmp_path):
        with pytest.raises(MediaError) as e:
            ensure_local("/etc/passwd", tmp_path)
        assert e.value.code == "MEDIA_URL_INVALID"


class TestExtractFrame:
    def test_invokes_ffmpeg_with_seek_and_single_frame(self, tmp_path):
        video = _mk_video(tmp_path)
        calls, fake = _capture_run(lambda p: p.write_bytes(b"PNG"))

        with patch("backend.app.services.media_service.subprocess.run", fake):
            out = extract_frame(video, 1.5, tmp_path)

        assert out.exists()
        args = calls[0]
        assert args[0] == "ffmpeg"
        assert "-ss" in args and "1.5" in args[args.index("-ss") + 1]
        assert "-frames:v" in args
        assert str(out) == args[-1]

    def test_invalid_time_rejected(self, tmp_path):
        video = _mk_video(tmp_path)
        with pytest.raises(MediaError):
            extract_frame(video, -1, tmp_path)


class TestExtractLastFrame:
    def test_invokes_ffmpeg_with_tail_seek_and_single_frame(self, tmp_path):
        video = _mk_video(tmp_path)
        calls, fake = _capture_run(lambda p: p.write_bytes(b"PNG"))

        with patch("backend.app.services.media_service.subprocess.run", fake):
            out = extract_last_frame(video, tmp_path)

        assert out.exists()
        args = calls[0]
        assert args[0] == "ffmpeg"
        # -sseof 前置于 -i：尾部定位取末帧
        assert args[args.index("-sseof") + 1] == "-1.0"
        assert args.index("-sseof") < args.index("-i")
        assert "-frames:v" in args
        assert str(out) == args[-1]
        # 产物落在 outputs/media 下
        assert out.parent == tmp_path / "media"

    def test_missing_output_raises_media_error(self, tmp_path):
        video = _mk_video(tmp_path)
        calls, fake = _capture_run(lambda p: None)  # 不产出文件

        with (
            patch("backend.app.services.media_service.subprocess.run", fake),
            pytest.raises(MediaError) as e,
        ):
            extract_last_frame(video, tmp_path)

        assert e.value.code == "FFMPEG_FAILED"
        assert calls  # ffmpeg 确实被调用过


class TestTrim:
    def test_copy_first_then_reencode_fallback(self, tmp_path):
        video = _mk_video(tmp_path)
        state = {"n": 0}
        calls: list[list[str]] = []

        def fake_run(args, **kwargs):  # noqa: ANN001, ANN202
            calls.append(args)
            state["n"] += 1
            if state["n"] == 1:
                # copy 失败：不产出文件
                class R1:
                    returncode = 1
                    stderr = "could not find tag codec"
                return R1()
            Path(args[-1]).write_bytes(b"MP4_OK")
            class R2:
                returncode = 0
                stderr = ""
            return R2()

        with patch("backend.app.services.media_service.subprocess.run", fake_run):
            out = trim(video, 1.0, 4.0, tmp_path)

        assert out.exists()
        assert len(calls) == 2
        assert "-c" in calls[0] and "copy" in calls[0]
        assert "libx264" in calls[1]
        # -t 用 duration（3.000），而非 -to（-ss 前置时输出时间轴语义）
        assert calls[0][calls[0].index("-t") + 1] == "3.000"

    def test_span_and_order_validated(self, tmp_path):
        video = _mk_video(tmp_path)
        with pytest.raises(MediaError):
            trim(video, 5.0, 5.0, tmp_path)  # end<=start
        with pytest.raises(MediaError):
            trim(video, 0.0, 10_000.0, tmp_path)  # 跨度超限

    def test_ffmpeg_failure_raises_media_error(self, tmp_path):
        video = _mk_video(tmp_path)

        def fake_run(args, **kwargs):  # noqa: ANN001, ANN202
            class R:
                returncode = 1
                stderr = "boom"
            return R()

        with (
            patch("backend.app.services.media_service.subprocess.run", fake_run),
            pytest.raises(MediaError) as e,
        ):
            trim(video, 0.0, 2.0, tmp_path)
        assert e.value.code == "FFMPEG_FAILED"


class TestSubtitleBurn:
    """P2-8: 字幕烧录——srt 文本落盘 + subtitles 滤镜 + 简单文本时长兜底。"""

    def test_srt_text_burned_with_subtitles_filter(self, tmp_path):
        from backend.app.services.media_service import burn_subtitles

        _mk_video(tmp_path)
        srt = "1\n00:00:00,000 --> 00:00:02,000\n你好世界\n"
        calls, fake = _capture_run(lambda p: p.write_bytes(b"OUT"))

        with patch("backend.app.services.media_service.subprocess.run", fake):
            out = burn_subtitles(tmp_path / "videos/a.mp4", srt, tmp_path)

        assert out.is_file()
        # ffmpeg 参数含 subtitles 滤镜，srt 路径转义
        joined = " ".join(calls[0])
        assert "subtitles=" in joined
        # srt 临时文件已写入内容
        srt_files = list((tmp_path / "media").glob("*.srt")) or list(tmp_path.rglob("*.srt"))
        assert srt_files, "srt 应落盘"

    def test_plain_text_gets_full_duration_srt(self, tmp_path):
        """纯文本（无 srt 格式）→ 全片一条字幕。"""
        from backend.app.services.media_service import burn_subtitles

        _mk_video(tmp_path)
        calls, fake = _capture_run(lambda p: p.write_bytes(b"OUT"))

        with patch("backend.app.services.media_service.subprocess.run", fake):
            burn_subtitles(tmp_path / "videos/a.mp4", "只有一句话", tmp_path)

        srt_file = next(p for p in tmp_path.rglob("*.srt"))
        content = srt_file.read_text(encoding="utf-8")
        assert "只有一句话" in content
        assert "-->" in content  # 生成了时间轴

    def test_empty_text_rejected(self, tmp_path):
        from backend.app.services.media_service import burn_subtitles

        _mk_video(tmp_path)
        with pytest.raises(MediaError) as e:
            burn_subtitles(tmp_path / "videos/a.mp4", "  ", tmp_path)
        assert e.value.code == "MEDIA_PARAMS_INVALID"


class TestZoom:
    """P2-8: 视频放大——中心裁剪 + 放回原分辨率。"""

    def test_zoom_crop_and_scale_args(self, tmp_path):
        from backend.app.services.media_service import zoom

        _mk_video(tmp_path)
        calls, fake = _capture_run(lambda p: p.write_bytes(b"OUT"))

        with patch("backend.app.services.media_service.subprocess.run", fake):
            out = zoom(tmp_path / "videos/a.mp4", 2.0, tmp_path)

        joined = " ".join(calls[-1])  # 最后一帧调用是 ffmpeg（此前有 ffprobe）
        assert "crop=" in joined
        assert "scale=" in joined
        assert out.is_file()

    def test_zoom_factor_validated(self, tmp_path):
        from backend.app.services.media_service import zoom

        _mk_video(tmp_path)
        with pytest.raises(MediaError) as e:
            zoom(tmp_path / "videos/a.mp4", 0.5, tmp_path)
        assert e.value.code == "MEDIA_PARAMS_INVALID"
        with pytest.raises(MediaError):
            zoom(tmp_path / "videos/a.mp4", 5.0, tmp_path)


class TestComposeVideo:
    """M3 成片合成三阶段契约：归一化 → concat → 可选 BGM（mock subprocess）。"""

    def test_rejects_unknown_resolution(self, tmp_path):
        with pytest.raises(MediaError) as e:
            compose_video(["/outputs/videos/a.mp4"], tmp_path, resolution="4k")
        assert e.value.code == "MEDIA_PARAMS_INVALID"

    def test_normalizes_concat_and_bgm_pipeline(self, tmp_path, monkeypatch):
        segs = ["/outputs/videos/a.mp4", "/outputs/videos/b.mp4"]
        monkeypatch.setattr(
            "backend.app.services.media_service.ensure_local",
            lambda url, root=None: Path(str(root or tmp_path)) / url.removeprefix("/outputs/"),
        )
        calls, fake = _capture_run(lambda p: p.write_bytes(b"MP4"))
        # ffprobe mock：返回 JSON 时长
        probe_calls = []
        concat_list_lines: list[str] = []

        def fake_run(args, **kwargs):  # noqa: ANN001
            if args[0].endswith("ffprobe"):
                probe_calls.append(args)
                class R:
                    returncode = 0
                    stderr = ""
                    stdout = '{"streams":[{"width":640,"height":384}],"format":{"duration":"6.0"}}'
                return R()
            # concat 调用时即时捕获列表内容（finally 会清目录）
            if any(str(a).endswith("list.txt") for a in args):
                list_arg = next(str(a) for a in args if str(a).endswith("list.txt"))
                concat_list_lines.extend(
                    Path(list_arg).read_text().strip().splitlines()
                )
            return fake(args, **kwargs)

        with (
            patch("backend.app.services.media_service.subprocess.run", fake_run),
            patch("backend.app.services.media_service.FFMPEG_BIN", "ffmpeg"),
        ):
            out = compose_video(
                segs, tmp_path, resolution="480p",
                bgm_url="/outputs/media/bgm.mp3",
            )

        assert out.exists()
        assert out.parent == tmp_path / "videos"
        assert out.name.startswith("compose_")
        # 归一化：两段都经过 scale+pad+fps+setsar 到 854x480，补静音轨
        norm_calls = [c for c in calls if c[0] == "ffmpeg" and "scale=" in " ".join(c)]
        assert len(norm_calls) == 2
        joined = " ".join(norm_calls[0])
        assert "854:480" in joined
        assert "force_original_aspect_ratio=decrease" in joined
        assert "pad=854:480" in joined
        assert "fps=30" in joined and "setsar=1" in joined
        assert "anullsrc" in joined and "libx264" in joined
        # ffprobe：每段取时长（duration）+ 探测音轨（has_audio，V6 fix）
        assert len(probe_calls) == 2 * len(segs)
        # concat：-f concat 且 -c copy
        concat_calls = [c for c in calls if "concat" in c]
        assert concat_calls, "缺少 concat 调用"
        assert "-c" in concat_calls[0] and "copy" in concat_calls[0]
        # 列表行必须是绝对路径（concat 相对列表文件目录解析，相对路径会嵌套失效）
        assert len(concat_list_lines) == 2
        for line in concat_list_lines:
            assert line.startswith("file '/") and line.endswith("'"), line
        # BGM：amix filter_complex 且 volume=0.25（按参数名匹配，防路径子串误伤）
        bgm_calls = [c for c in calls if "-filter_complex" in c]
        assert len(bgm_calls) == 1
        fc = bgm_calls[0][bgm_calls[0].index("-filter_complex") + 1]
        assert "amix=inputs=2" in fc and "volume=0.25" in fc
        # 中间文件清理
        assert not (tmp_path / "videos" / "norm_1.mp4").exists()

    def test_no_bgm_skips_amix(self, tmp_path, monkeypatch):
        monkeypatch.setattr(
            "backend.app.services.media_service.ensure_local",
            lambda url, root=None: Path(str(root or tmp_path)) / "x.mp4",
        )
        calls, fake = _capture_run(lambda p: p.write_bytes(b"MP4"))

        def fake_run(args, **kwargs):  # noqa: ANN001
            if args[0].endswith("ffprobe"):
                class R:
                    returncode = 0
                    stderr = ""
                    stdout = '{"format":{"duration":"6.0"}}'
                return R()
            return fake(args, **kwargs)

        with patch("backend.app.services.media_service.subprocess.run", fake_run):
            out = compose_video(["/outputs/videos/x.mp4"], tmp_path)

        assert not [c for c in calls if "-filter_complex" in c]
        assert out.exists()
