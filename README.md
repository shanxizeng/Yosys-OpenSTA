# OpenSTA 关键路径分析流程

对 RTL 设计自动生成寄存器/组合包装，使用 yosys 综合并映射到工艺库，再用 OpenSTA 分析关键路径的自动化流程。

## 环境要求

- yosys（含 oss-cad-suite）
- OpenSTA 3.1
- 工艺库 Liberty 文件（默认 nangate45）

环境变量通过 `settings_OpenSTA.sh` 配置：

```bash
source ~/settings_OpenSTA.sh
```

## 快速开始

```bash
source ~/settings_OpenSTA.sh
python3 run_flow.py              # 按 flow.env 配置跑全流程
python3 run_flow.py --clean      # 清空输出目录后重跑
python3 run_flow.py -c 其他.env  # 使用其他配置文件
```

将待分析的 RTL（`.sv`/`.v`）放入源码目录（默认 `verilogs/`），按文件名排序的第一个文件作为 top 模块。

## 流程

```
flow.env ──► run_flow.py
               ├─ 1. 扫描 SRC_DIR 下所有 .sv/.v（排除 *_wrapper），确定 top 文件
               ├─ 2. 调用 yosys/auto_{reg_,}wrapper.py 生成包装 → OUT_DIR
               ├─ 3. 生成 run.ys，运行 yosys（proc/opt/synth/dfflibmap/abc）
               ├─ 4. 按 CLK_PERIOD 替换 SDC 时钟周期，生成 OUT_DIR/sta.tcl
               ├─ 5. 运行 OpenSTA，报告写入 OUT_DIR/sta_timing_report.log
               └─ 6. 终端打印时序报告摘要
```

任一步骤失败立即退出并报告错误。

## 目录结构

```
├── run_flow.py              # 全流程自动化总入口
├── flow.env                 # 环境配置文件
├── constraints.sdc          # 时序约束（时钟周期由 CLK_PERIOD 自动替换）
├── verilogs/                # 源代码目录（可配置）
├── yosys/
│   ├── auto_wrapper.py          # 纯组合包装生成器
│   ├── auto_reg_wrapper.py      # 寄存器包装生成器
│   └── test.py                  # 旧单步入口（已由 run_flow.py 取代）
└── output/                  # 所有生成物（已加入 .gitignore）
```

## 配置说明（flow.env）

| 配置项 | 默认值 | 说明 |
|---|---|---|
| `YOSYS_BIN` / `STA_BIN` | 空 | 工具路径，留空使用 PATH |
| `LIBERTY` | /tools/nangate45/NangateOpenCellLibrary_typical.lib | 工艺库路径 |
| `SDC` | constraints.sdc | 时序约束文件 |
| `SRC_DIR` | verilogs | 源码目录 |
| `TOP` | 空 | top 文件名；留空取源码目录第一个文件 |
| `CLK_PERIOD` | 10.0 | 时钟周期 (ns)，自动替换 SDC 中 create_clock 的 period |
| `REG_MODE` | 1 | 1 = 寄存器包装；0 = 纯组合包装 |
| `OUT_DIR` | output | 输出目录 |
| `PATH_COUNT` | 10 | 报告的最差路径条数 |

## 两种包装方式

- **REG_MODE=1（寄存器包装）**：每个 DUT 输入信号经顶层 `{name}_in` 端口驱动一级输入寄存器，DUT 输出接一级输出寄存器。STA 分析 **输入寄存器 Q → DUT 组合逻辑 → 输出寄存器 D** 的 reg2reg 关键路径。
- **REG_MODE=0（纯组合包装）**：输入/输出信号直连顶层 `{name}_in` / `{name}_out` 端口，不加寄存器。此模式下无 reg2reg 路径，STA 报告 `No paths found` 属预期行为；如需分析组合路径，请在 `constraints.sdc` 中补充 `set_input_delay` / `set_output_delay` 约束。

## 输出产物（output/）

| 文件 | 说明 |
|---|---|
| `{top}[_reg]_wrapper.sv` | 生成的包装文件 |
| `run.ys` | yosys 综合脚本 |
| `synthesized_netlist.v` | 门级网表（映射到 LIBERTY 库） |
| `constraints.sdc` | 替换时钟周期后的约束 |
| `sta.tcl` | OpenSTA 分析脚本 |
| `sta_timing_report.log` | 时序报告（最差路径 + summary） |

## 注意事项

- yosys 不识别 Vivado 的 `(* DONT_TOUCH = "YES" *)` 属性，包装生成器使用 `(* keep *)`；悬空寄存器会被 yosys 折叠为 x，包装器必须保证每个寄存器都有真实驱动。
- 源码目录下以 `_wrapper` 结尾的文件会被自动排除，避免与生成的包装模块重名。
