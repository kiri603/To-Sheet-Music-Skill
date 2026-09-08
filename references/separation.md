# 分轨脚本设计与使用

## 为什么采用两组分析

`high` 默认先运行 `htdemucs_ft`，再运行 `htdemucs_6s`；两次均读取同一份标准化输入。四源结果作为人声、贝斯、鼓和其他伴奏的基础材料，六源结果额外提供吉他和钢琴候选。保留双方输出，冲突处回到混音和两组分离结果对比。

六源模型的 `piano` 并不覆盖所有键盘音色，也可能存在串音；`guitar` 不是天然区分主音/节奏的两个声部。对合成器、弦乐铺底等，还须检查 `other`。这是分析素材的选择，不能预设某组结果对每首歌都更准确。

**禁止做法**：复制吉他轨后分别命名主音与节奏；把高音全部交给主音、低音全部交给节奏；反复串联分离器造成音符消失却没有对照原混音；把不同模型的全部轨道相加作为伴奏。

## 运行

依赖准备见 [toolchain.md](toolchain.md)。在装有 Demucs、NumPy、SoundFile 的 Python 环境中运行：

```text
python /path/to/skill/scripts/separate_audio.py --input /path/to/song.wav --out-dir work/separation-v1 --profile high --device auto
```

包含空格或中文的路径必须加引号。Windows PowerShell 示例：

```powershell
$OutputEncoding = [Console]::OutputEncoding = [Text.UTF8Encoding]::new($false)
$env:PYTHONUTF8 = '1'
& 'C:/path/to/.venv/Scripts/python.exe' 'C:/path/to/skill/scripts/separate_audio.py' --input 'C:/audio/歌曲.wav' --out-dir 'C:/task/work/separation-v1' --profile high --device auto --model-cache 'C:/task/work/models'
```

默认参数：44.1 kHz、双声道、32 位浮点 WAV；模型内部片段 7 秒、重叠 0.5、1 次 shift、随机种子 17、CPU 线程 4。固定种子有助于复查，不承诺跨设备逐位相同。GPU 可用则使用 CUDA；没有 GPU 时自动用 CPU，不能将 GPU 视为收费前提。

`--profile balanced` 只运行六源模型，适合内部试跑或资源不足时明确记录的替代方案。不要因为方便就静默把完整处理改成片段或更快档位。`--start 60 --duration 15` 用于复查原音频 60 秒起的 15 秒片段；该输出不是整首歌，事件时间必须再加回 60 秒。

## 数据流与输出

```text
用户原音频（只读）
  └─ FFmpeg 解码：mixture.wav，共同时间零点，不裁剪静音，不压缩动态
       ├─ htdemucs_ft/{vocals,drums,bass,other}.wav
       └─ htdemucs_6s/{vocals,drums,bass,other,guitar,piano}.wav
           └─ manifest.json → 角色候选 → 节拍/和声/音符分析 → 乐手声部编配
```

脚本通过 Demucs Python API 保存模型结果，避免命令行逐轨 rescale/clamp 改变音量关系。浮点轨道的峰值可以超过 1；这是需检查的信号统计，不能直接视为 PCM 已削波。若需试听，另制作使用共同增益的副本，分析轨道保留不变。

`manifest.json` 保存源路径和 SHA-256、实际采样率/帧数/时长、片段偏移、模型与库版本、参数、运行设备、CPU 重试、每轨相对路径、能量及重建残差。`roles` 是跨模型选择出的分析入口，不是一组可以直接求和的互斥 stems。

- 每轨必须与 `mixture.wav` 有相同帧数、采样率和声道数；写盘后重新检查。
- 不自动修剪或拉伸长度不同的结果来掩盖错误；不把某轨能量小当作“该乐器不存在”的充分证据。
- 重建残差仅用于发现异常，它低不代表分离干净；没有原始真实 stems 时不得报告 SDR/SIR 等依赖真实参考的准确率。
- `sample_timeline_verified` 证明文件长度一致，不证明每个瞬态相位或音符起点准确；还要复查鼓重拍、吉他起音等锚点。

## 恢复与资源管理

使用新输出目录；已存在的目录会被拒绝。过程中逐模型保存 manifest，失败时标记 `failed` 并保留已经成功的分析文件；不要把它当完整结果。重试可使用新目录并沿用模型缓存，避免重复下载。

下载连接错误最多三次尝试，永久 HTTP 错误直接报告；不要关闭 TLS 验证。CUDA OOM 仅将当前模型转 CPU 重试一次，缩短模型内部片段，仍保留整首输入。内存不足、缺少依赖或模型不兼容时记录准确原因，按工具文档选择兼容免费环境，不能生成空 stems 假装成功。

跨任务可复用模型缓存，不能将上次歌曲的 stems 当成本次结果；复用音频分析前必须核对输入哈希、截取范围、模型和参数。

模型文件不随 skill 打包。首次运行按需从官方来源下载，音频不上传；免费指不依赖新增付费工具或服务，不代表本地没有处理时间和资源消耗。

## 依据

- [Demucs 官方项目](https://github.com/adefossez/demucs)：模型说明、六源限制、Python API、模型参数与许可。
- [原始 Demucs 实现](https://github.com/facebookresearch/demucs)：旧环境 API 参考；新安装前检查维护版本的兼容说明。

文档核实日期：2026-09-07。脚本实际兼容性以当前环境 smoke test 为准，不把网页示例替代本机验证。
