"""(v15) Assemble the demo video from slide images and console frames, with burned-in subtitles.

Inputs: slide PNGs (1920x1080) for the cover, example, positioning, ID, impact and deployment slides; the console
frames and frames.json written by capture_frames_v6.js. Output: an H.264 MP4 (no audio track),
an .srt with the same lines, and the narration text. A recorded narration can be added later with
  ffmpeg -i video.mp4 -i narration.m4a -c:v copy -c:a aac -shortest video_with_voice.mp4
Usage: python build_video_v15.py --slides <dir with slide-01.png ...> --frames <frames dir> --out <dir>
"""
import argparse, json, subprocess, tempfile
from pathlib import Path

NARRATION = {
    "S1": ["这是同模共振 EchoGuard，", "银行在财富管理 AI 应用上线前和变更后使用的行为测试与复核工具。"],
    "S2": ["同一位高风险客户交给 DeepSeek、千问和 Kimi 配置基金，", "9 次推荐里有 8 次包含沪深 300 组里排在最前的那只。", "展示顺序成了 AI 实际的推荐依据。"],
    "S2B": ["EchoGuard 不直接面向客户：", "复核结论进入准入审批，通过后客户侧 AI 应用才上线；", "处置规则写进银行的产品列表接口，不修改外部模型。"],
    "S3": ["打开测试台。首批的风险卡给出人工复核：", "三个画像的相似度明显超出只按经济条件选择时的基准。", "极保守画像三家高度一致，但基准同样高，不报警。"],
    "S4": ["点开高风险边界画像，", "可以看到每次推荐的原始响应、模型 ID 和响应 ID。", "红褐色标出的，是所在组展示在最前、又被选中的产品。"],
    "S5": ["预先冻结的对照实验让每家模型看到各自的随机顺序。", "复测中超出基准的 5 个画像降为 0 个。", "各家模型仍偏好自己看到的第一个，因此判为观察。"],
    "S6": ["三家的首位偏好在精确检验中均显著；", "比例下限由银行设定。", "调到 90% 时，只剩千问的 94% 仍触发观察。"],
    "S7": ["配置对比：8 个画像的跨模型相似度全部下降，p=0.0039。", "高风险边界画像从 0.49 降到 0.12，低于条件基准。"],
    "S8": ["在提示词里说明顺序不代表优先级，", "首位比例只从 83% 降到 80%，回归报告建议不采用。"],
    "S8B": ["换用另一套 12 只基金和 9 个新画像，", "独立排序后 9 个画像的相似度全部下降，p=0.002。", "只接一家模型时，统一排序下排在首位的三只产品拿到 67% 的单选，", "改为每次请求随机排序后，同样三只降到 16%。"],
    "S9": ["复核人员选择归类和处理建议，", "导出复核记录，附入模型准入材料。"],
    "S9A": ["受控实验中，编号最小的产品被优先选择；", "换成随机编号后，被选比例从 91% 降到 32%。"],
    "S9B": ["换算成资金：只接一家模型、所有请求用同一顺序时，", "按全部采纳、等额资金换算，", "每 1 亿元推荐资金中约 2,700 万元的去向随排序改变。"],
    "S10": ["EchoGuard 部署在银行测试环境中，接在准入和变更流程里。", "一次测试 81 次调用，API 费用约 1 元；", "最终决定由银行既有审批作出。"],
}
SLIDES = [("S1", "slide-01.png", 12.0), ("S2", "slide-02.png", 18.0), ("S2B", "slide-03.png", 13.0)]
END = [("S9A", "slide-14.png", 11.0), ("S9B", "slide-15.png", 13.0), ("S10", "slide-18.png", 15.0)]


def ts_srt(t):
    h, rem = divmod(t, 3600); m, s = divmod(rem, 60)
    return f"{int(h):02d}:{int(m):02d}:{int(s):02d},{int(round((s - int(s)) * 1000)):03d}"


def ts_ass(t):
    h, rem = divmod(t, 3600); m, s = divmod(rem, 60)
    return f"{int(h)}:{int(m):02d}:{s:05.2f}"


def main():
    ap = argparse.ArgumentParser(description=__doc__)
    ap.add_argument("--slides", type=Path, required=True)
    ap.add_argument("--frames", type=Path, required=True)
    ap.add_argument("--out", type=Path, required=True)
    ap.add_argument("--name", default="EchoGuard_演示视频")
    a = ap.parse_args()
    a.out.mkdir(parents=True, exist_ok=True)
    frames = json.loads((a.frames / "frames.json").read_text(encoding="utf-8"))
    seq = [(sc, a.slides / f, d) for sc, f, d in SLIDES]
    seq += [(f["scene"], a.frames / f["file"], f["seconds"]) for f in frames]
    seq += [(sc, a.slides / f, d) for sc, f, d in END]

    # scene windows
    windows, t = {}, 0.0
    for sc, _, d in seq:
        s0, s1 = windows.get(sc, (t, t))
        windows[sc] = (min(s0, t), t + d)
        t += d
    total = t

    # subtitles: lines split each scene window in proportion to their length
    cues = []
    for sc, lines in NARRATION.items():
        s0, s1 = windows[sc]
        s0 += 0.4; s1 -= 0.3
        weights = [max(len(x), 8) for x in lines]
        acc = s0
        for line, w in zip(lines, weights):
            dur = (s1 - s0) * w / sum(weights)
            cues.append((acc, acc + dur, line)); acc += dur
    srt = "\n".join(f"{i}\n{ts_srt(s)} --> {ts_srt(e)}\n{txt}\n" for i, (s, e, txt) in enumerate(cues, 1))
    (a.out / f"{a.name}.srt").write_text(srt, encoding="utf-8")
    ass = ["[Script Info]", "ScriptType: v4.00+", "PlayResX: 1920", "PlayResY: 1080", "WrapStyle: 0", "",
           "[V4+ Styles]",
           "Format: Name, Fontname, Fontsize, PrimaryColour, SecondaryColour, OutlineColour, BackColour, Bold, Italic, Underline, StrikeOut, ScaleX, ScaleY, Spacing, Angle, BorderStyle, Outline, Shadow, Alignment, MarginL, MarginR, MarginV, Encoding",
           "Style: Sub,Noto Serif CJK SC,46,&H00FFFFFF,&H00FFFFFF,&H281D2123,&H00000000,0,0,0,0,100,100,1,0,3,14,0,2,160,160,56,1",
           "", "[Events]", "Format: Layer, Start, End, Style, Name, MarginL, MarginR, MarginV, Effect, Text"]
    ass += [f"Dialogue: 0,{ts_ass(s)},{ts_ass(e)},Sub,,0,0,0,,{txt}" for s, e, txt in cues]
    tmp = Path(tempfile.mkdtemp())
    (tmp / "subs.ass").write_text("\n".join(ass) + "\n", encoding="utf-8")
    narration = "\n\n".join(f"[{sc} {ts_srt(windows[sc][0])[:8]}–{ts_srt(windows[sc][1])[:8]}]\n" + "".join(lines) for sc, lines in NARRATION.items())
    (a.out / f"{a.name}_旁白.txt").write_text(narration + "\n", encoding="utf-8")

    concat = tmp / "list.txt"
    with open(concat, "w", encoding="utf-8") as f:
        for _, p, d in seq:
            f.write(f"file '{p.resolve()}'\nduration {d:.3f}\n")
        f.write(f"file '{seq[-1][1].resolve()}'\n")
    out = a.out / f"{a.name}.mp4"
    cmd = ["ffmpeg", "-y", "-hide_banner", "-loglevel", "error", "-f", "concat", "-safe", "0", "-i", str(concat),
           "-vf", f"scale=1920:1080:force_original_aspect_ratio=decrease,pad=1920:1080:(ow-iw)/2:(oh-ih)/2:color=F7F5F0,fps=30,format=yuv420p,ass={tmp / 'subs.ass'}",
           "-c:v", "libx264", "-preset", "medium", "-crf", "20", "-movflags", "+faststart", "-t", f"{total:.2f}", str(out)]
    subprocess.run(cmd, check=True)
    print(out, f"{total:.1f} s,", len(cues), "subtitle lines")


if __name__ == "__main__":
    main()
