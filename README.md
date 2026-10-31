# pinshuai-batch-image

用已经写好的提示词 TXT 加上参考图，通过 OpenAI 兼容的第三方中转站批量出效果图。不重新写提示词：提示词由 [圣诞珍珠画01](https://github.com/Kangaroochan/christmas-pearl-painting-01) 之类的提示词 skill 事先生成。

默认每条提示词先试出 1 张，看图检查后再按指定张数批量出。出图后的检查结果和反馈会分别记进本 skill 和提示词 skill 各自的经验文件，下次出图、下次写提示词前会先读。

## 安装

克隆到 Codex 的技能目录，文件夹名保持 `pinshuai-batch-image`：

```powershell
git clone https://github.com/Kangaroochan/pinshuai-batch-image.git "$env:USERPROFILE\.codex\skills\pinshuai-batch-image"
```

需要 Python 3（只用标准库，不用装依赖）。裁图脚本 `scripts/crop.ps1` 需要 Windows PowerShell。

## 配置

在出图的工作目录（或 jobs.json 所在文件夹及其上级）放一个 `.env`：

```
OPENAI_API_KEY=你的 key
OPENAI_BASE_URL=https://你的中转站地址/v1
```

key 只从 `.env` 或环境变量读取，不会写进日志、`run.json` 或报告。`.env` 不要提交到仓库。

## 使用

在 Codex 里调用 `$pinshuai-batch-image`，给出参考图，提示词放在参考图旁边，命名为 `<参考图名>-提示词.txt`（多个版本 `-提示词-2.txt`、`-3.txt` 时默认用最新版）。

脚本也可以单独用：

```powershell
python scripts/make_jobs.py --images 1.webp 2.webp      # 配对提示词和参考图，写出 jobs.json
python scripts/render.py jobs.json --dry-run            # 检查，不联网
python scripts/render.py jobs.json --count 1            # 试跑，每条 1 张
python scripts/render.py jobs.json --count 5            # 正式批量
```

图片、`run.json`、`report.md` 和 `visual-review.json` 存在 `<jobs.json 所在文件夹>/出图/<时间戳>/`。

## 文件

- `SKILL.md`：skill 规则
- `scripts/make_jobs.py`：配对提示词和参考图
- `scripts/render.py`：调用中转站出图，有参考图走 `/images/edits`，没有走 `/images/generations`
- `scripts/crop.ps1`：用户要求时裁出产品
- `references/lessons.md`：出图经验记录，里面是作者自己的出图记录，可以清空成模板再用

## 许可

MIT
