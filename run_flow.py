#!/usr/bin/env python3
"""OpenSTA 全流程自动化。

流程: 读 flow.env -> 扫描源码目录 -> 生成 wrapper -> yosys 综合 -> OpenSTA 时序分析
所有生成物集中输出到 OUT_DIR（默认 output/）。

用法:
    python3 run_flow.py              # 按 flow.env 跑全流程
    python3 run_flow.py --clean      # 先清空输出目录再跑
"""
import argparse
import re
import shutil
import subprocess
import sys
from pathlib import Path

ROOT = Path(__file__).resolve().parent


def parse_env(path):
    """解析 env/shell 风格配置文件，支持 # 注释。"""
    cfg = {}
    for line in Path(path).read_text().splitlines():
        line = line.split('#', 1)[0].strip()
        if not line or '=' not in line:
            continue
        key, value = line.split('=', 1)
        cfg[key.strip()] = value.strip()
    return cfg


def run(cmd, cwd=None):
    print('+', ' '.join(map(str, cmd)))
    result = subprocess.run([str(c) for c in cmd], cwd=cwd)
    if result.returncode != 0:
        sys.exit(f"error: 命令失败 (exit {result.returncode}): {' '.join(map(str, cmd))}")


def make_sta_tcl(cfg, netlist, top_module, sdc, report, path_count):
    """生成 OpenSTA 分析脚本。"""
    return f"""read_liberty {cfg['LIBERTY']}
read_verilog {netlist}
link_design {top_module}
read_sdc {sdc}
report_checks -path_delay max -group_path_count {path_count} -sort_by_slack -format full > {report}
report_checks -path_delay max -group_path_count 1 -format summary >> {report}
exit
"""


def main():
    parser = argparse.ArgumentParser(description='OpenSTA 全流程自动化')
    parser.add_argument('-c', '--config', default='flow.env', help='配置文件路径（默认 flow.env）')
    parser.add_argument('-q', '--quiet', action='store_true', help='静默输出')
    parser.add_argument('--clean', action='store_true', help='运行前清空输出目录')
    args = parser.parse_args()

    # ---------- 读配置 ----------
    cfg_path = ROOT / args.config
    if not cfg_path.exists():
        sys.exit(f'error: 找不到配置文件 {cfg_path}')
    cfg = parse_env(cfg_path)

    src_dir = ROOT / cfg.get('SRC_DIR', 'verilogs')
    out_dir = ROOT / cfg.get('OUT_DIR', 'output')
    reg_mode = cfg.get('REG_MODE', '1') == '1'
    clk_period = float(cfg.get('CLK_PERIOD', '10.0'))
    path_count = cfg.get('PATH_COUNT', '10')

    # ---------- 工具路径（配置优先，缺省用 PATH） ----------
    yosys_bin = cfg.get('YOSYS_BIN') or shutil.which('yosys')
    sta_bin = cfg.get('STA_BIN') or shutil.which('sta')
    if not yosys_bin:
        sys.exit('error: 找不到 yosys，请先配置 PATH 环境变量，或在 flow.env 里指定 YOSYS_BIN')
    if not sta_bin:
        sys.exit('error: 找不到 sta，请先配置 PATH 环境变量，或在 flow.env 里指定 STA_BIN')
    if not Path(cfg['LIBERTY']).exists():
        sys.exit(f"error: 找不到工艺库 {cfg['LIBERTY']}")

    # ---------- 输出目录 ----------
    if args.clean and out_dir.exists():
        shutil.rmtree(out_dir)
    out_dir.mkdir(parents=True, exist_ok=True)

    # ---------- 扫描源码，确定 top 文件 ----------
    if not src_dir.is_dir():
        sys.exit(f'error: 源码目录不存在 {src_dir}')
    # 排除以 _wrapper 结尾的生成文件，避免与本次生成的 wrapper 模块重名
    src_files = sorted(
        p for p in src_dir.iterdir()
        if p.suffix in ('.sv', '.v') and not p.stem.endswith('_wrapper')
    )
    if not src_files:
        sys.exit(f'error: {src_dir} 下没有 .sv/.v 文件')
    top_name = cfg.get('TOP')
    if top_name:
        top_src = src_dir / top_name
        if not top_src.exists():
            sys.exit(f'error: 指定的 top 文件不存在 {top_src}')
    else:
        top_src = src_files[0]
    # TOP_MODULE 留空 = 用 top 文件 stem 推断模块名
    top_module = cfg.get('TOP_MODULE') or top_src.stem
    wrapper_module = f'{top_module}_wrapper'
    print(f'top 模块: {top_module} ({top_src})')

    # ---------- 1. 生成 wrapper ----------
    wrapper_script = ROOT / 'yosys' / ('auto_reg_wrapper.py' if reg_mode else 'auto_wrapper.py')
    print('\n== 生成 wrapper ==')
    run([sys.executable, wrapper_script, top_src, out_dir, top_module])
    wrapper_file = out_dir / f'{top_module}{"_reg" if reg_mode else ""}_wrapper.sv'
    if not wrapper_file.exists():
        sys.exit(f'error: wrapper 未生成 {wrapper_file}')

    # ---------- 2. yosys 综合 ----------
    ys_lines = [f'read_verilog -sv {wrapper_file}']
    for f in src_files:
        ys_lines.append(f'read_verilog -sv {f}' if f.suffix == '.sv' else f'read_verilog {f}')
    netlist = out_dir / 'synthesized_netlist.v'
    stats_json = out_dir / 'stats.json'
    stats_txt = out_dir / 'stats.txt'
    ys_lines += [
        f'hierarchy -check -top {wrapper_module}',
        'proc; opt; fsm; opt; memory; opt',
        'synth -flatten',
        f'dfflibmap -liberty {cfg["LIBERTY"]}',
        f'abc -liberty {cfg["LIBERTY"]}',
        f'write_verilog -noattr {netlist}',
        f'tee -o {stats_json} stat -json',
        f'tee -o {stats_txt} stat'
    ]
    ys_path = out_dir / 'run.ys'
    ys_path.write_text('\n'.join(ys_lines) + '\n')
    print('\n== yosys 综合 ==')
    if args.quiet:
        run([yosys_bin, '-Q', '-q', ys_path])
    else:
        run([yosys_bin, ys_path])

    # ---------- 3. 约束（替换时钟周期） ----------
    sdc_src = ROOT / cfg['SDC']
    if not sdc_src.exists():
        sys.exit(f'error: 找不到约束文件 {sdc_src}')
    sdc_text = sdc_src.read_text()
    sdc_text = re.sub(r'(-period\s+)[\d.]+', rf'\g<1>{clk_period}', sdc_text)
    sdc_out = out_dir / 'constraints.sdc'
    sdc_out.write_text(sdc_text)

    # ---------- 4. OpenSTA 时序分析 ----------
    report = out_dir / 'sta_timing_report.log'
    sta_tcl = out_dir / 'sta.tcl'
    sta_tcl.write_text(make_sta_tcl(cfg, netlist, wrapper_module, sdc_out, report, path_count))
    print('\n== OpenSTA 时序分析 ==')
    run([sta_bin, '-no_splash', '-exit', sta_tcl])

    # ---------- 5. 摘要 ----------
    print('\n== 时序报告摘要（%s） ==' % report.relative_to(ROOT))
    tail = report.read_text().splitlines()[:30]
    print('\n'.join(tail))
    print(f'\n流程完成，全部生成物在 {out_dir.relative_to(ROOT)}/')


if __name__ == '__main__':
    main()
