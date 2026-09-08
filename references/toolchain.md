# 免费工具与运行方式

先查已有环境，不固定用户机器路径，不把上次任务的环境路径写入 skill。脚本自身不自动安装软件，也不包含模型权重。

## 分轨环境

需要 Python、NumPy、SoundFile、PyTorch、Demucs；FFmpeg 可以通过系统可执行文件、`--ffmpeg` 参数或 `imageio-ffmpeg` 提供。

优先复用已能工作的环境。需要新建时，使用任务目录内独立 venv，并先核实目标 Python/系统对应的官方依赖说明。Python 3.11 可作为本工作流的兼容起点；不要将要求较旧 Python 的 Basic Pitch 强行装进最新系统 Python。

```text
python3.11 -m venv work/audio-env
work/audio-env/bin/python -m pip install demucs numpy soundfile imageio-ffmpeg
work/audio-env/bin/python /path/to/skill/scripts/separate_audio.py --help
```

Windows 将 venv 内路径换成 `work/audio-env/Scripts/python.exe`，PowerShell 通过 `& '完整路径'` 调用。缺少 Python 3.11 时先检查已有解释器与包兼容性，不能原样运行不存在的命令。CUDA 是可选加速；根据现有驱动和 PyTorch 官方安装说明选兼容版本，避免替换用户已有 GPU 环境。

本技能制作时，Demucs 实际 API 测试环境使用 4.0.1；官方维护仓库在 2026-09-07 查询时已发布 4.1.0。已有环境可保留经验证的版本；新安装要运行短片段测试并记录实际版本，不能假定不同版本权重下载地址和依赖完全相同。

## 音高、节拍与转录

可按任务需要使用 `librosa`、`basic-pitch`、`pretty_midi`、`mido`、`music21`。不要为了简单扒谱一次性安装所有研究模型。

Basic Pitch 适合作为单乐器分离素材的音符候选生成器；对完整混音、失真吉他和钢琴串音的结果必须额外检查。使用同一识别器多跑几次不算独立证据。

```text
python -m pip install basic-pitch librosa pretty_midi mido music21
basic-pitch --help
basic-pitch work/candidates/bass work/separation/htdemucs_ft/bass.wav
basic-pitch work/candidates/guitar work/separation/htdemucs_6s/guitar.wav
basic-pitch work/candidates/keys work/separation/htdemucs_6s/piano.wav
```

实际 stem 路径从 `manifest.json` 读取；命令示例中的目录名不应写死在实现里。若与分轨环境存在依赖冲突，创建独立转录 venv，通过 WAV/MIDI 文件传递数据。只按需要安装；安装、推理或分轨失败都不能用编造音符替代。

贝斯可用单音基频估计交叉检查八度；吉他按短乐句对照频谱、起音和和弦；键盘结合 bass 与和声重建双手分工。鼓不用 Basic Pitch，见 [arranging.md](arranging.md)。有已验证、可免费运行的专用转录模型时可以替换候选生成器，但继续执行相同校验与编配规则。

## 记谱与导出

MuseScore Studio 是本工作流的免费记谱和导出工具；不需要 MuseScore 网站付费会员或收费音色。先发现本机可执行文件：PATH、正在运行进程的可执行路径、操作系统常规安装目录或用户已指定的路径。Windows 常见文件名为 `MuseScore4.exe`，不要假定安装在 C 盘。

从整理好的 MusicXML 或原生 MSCX 构建谱面，使用当前安装版本实际试验导入与导出。`-o` 使用目标扩展名选择输出格式；生成 MSCZ 后从它导出 PDF 和 MIDI。不要使用 `-F` 清除用户设置，不用 `--force` 掩盖损坏谱面。日志与工作文件留在本次工作目录。

PDF 检查可用 Poppler 的 `pdftoppm`，结构读取用 `pypdf`。MIDI 检查可用 `mido` 或 `pretty_midi`。这些都只是技术校验工具，不提供音乐准确率保证。

## 随包验证

在分轨依赖环境中可运行 `python -m unittest discover -s /path/to/skill/scripts -p "test_*.py" -v`。随包测试覆盖静音/反相信号、分轨长度、下载重试边界、弦品对应、同弦冲突、小节数量与导出保护。

制作时已用 6 秒自行合成的混音实际运行四源、六源两组模型，检查 10 条输出轨道长度；另外用合成谱通过 MuseScore 实际导出三格式，校验 MIDI 的 96 个发音事件并查看 PDF。这里验证的是工具流程与谱面结构；尚未以一首真实混音歌曲完成从音频到定稿的还原度验收，不能将这些测试解释为音乐准确率证明。

## 官方依据

- [Demucs](https://github.com/adefossez/demucs)：分离模型、许可、安装与 API。
- [Basic Pitch](https://github.com/spotify/basic-pitch)：单乐器适用范围、CLI 与 Python 兼容说明。
- [librosa 节拍检测](https://librosa.org/doc/0.11.0/beat.html)：节拍候选接口；仍需处理半速、倍速和弱起。
- [MuseScore 命令行](https://musescore.org/en/print/book/export/html/329750)：转换参数；该页包含沿用的旧版内容，实际本机输出仍须验证。
- [MuseScore 文件导出](https://handbook.musescore.org/en_gb/file-management/file-export)：原生和交换文件格式。
- [MusicXML TAB](https://www.w3.org/2021/06/musicxml40/tutorial/tablature/)：弦、品、节奏与调弦表示。

核实日期：2026-09-07。只从官方来源获取工具和模型；版本变化时更新运行记录。
