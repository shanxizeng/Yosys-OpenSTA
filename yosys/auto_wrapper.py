#!/usr/bin/env python3
import re
import sys
from pathlib import Path

def parse_ports(file_path, module_name):
    with open(file_path, 'r') as f:
        content = f.read()
    # 剥离行注释，避免注释内容干扰端口解析
    content = re.sub(r'//[^\n]*', '', content)
    # 按目标模块名定位（文件中可能包含多个 module，目标未必在最前面）
    module_match = re.search(
        r'module\s+' + re.escape(module_name) + r'\s*\((.*?)\);', content, re.DOTALL)
    if not module_match:
        modules = re.findall(r'module\s+(\w+)\s*\(', content)
        raise ValueError(
            f"在 {file_path} 中找不到模块 {module_name}，"
            f"文件中的模块有: {', '.join(modules)}")
    ports_text = module_match.group(1)
    # 简单提取所有端口（忽略跨行细节，但通常OK）
    # 更好的做法是逐行处理，但为简化，此处用正则匹配所有 input/output
    # 注意：要处理可能的多行端口定义，这里采用非贪婪匹配
    lines = ports_text.split('\n')
    full_decl = ' '.join(lines).replace('\n',' ')
    # 正则提取 input/output 以及可能的位宽和名称
    pattern = r'(input|output)\s*(?:\[(\d+:\d+)\]\s*)?(\w+)'
    inputs = []
    outputs = []
    hasclock = False
    hasreset = False
    for m in re.finditer(pattern, full_decl):
        dir_, range_, name = m.groups()
        if name in ('clock', 'reset'):
            if name == 'clock':
                hasclock = True
            if name == 'clk':
                hasclk = True
            if name == 'reset':
                hasreset = True
            if name == 'rst':
                hasrst = True
            continue
        if dir_ == 'input':
            inputs.append((name, range_))
        else:
            outputs.append((name, range_))
    return inputs, outputs, hasclock, hasreset

def width_from_range(range_str):
    if not range_str:
        return 1
    msb, lsb = map(int, range_str.split(':'))
    return abs(msb - lsb) + 1

def generate_wrapper(inputs, outputs, hasclock, hasclk, hasreset, hasrst, module_name):
    num_inputs = len(inputs)
    num_outputs = len(outputs)
    # 地址宽度要能覆盖输入和输出寄存器
    addr_bits = (num_inputs + num_outputs).bit_length()
    # 如果地址总数为0，则设为1位避免0位宽
    if addr_bits == 0: addr_bits = 1

    wrapper = f"""// Generated Combinational Wrapper for {module_name}
// Each input signal is driven by a top-level input port <name>_in.
// Each output signal drives a top-level output port <name>_out.
// No registers are added; the module under test is wrapped as-is.
// No packed structs, no dynamic part-select. Fully synthesizable.

module {module_name}_wrapper #(
    parameter DATA_WIDTH = 64
//  parameter DATA_WIDTH = 32
)(
    input  logic                     clk,
    input  logic                     rst_n          // active low
"""
    for name, range_ in inputs:
        w = width_from_range(range_)
        if w == 1:
            wrapper += f",\n    input  logic                     {name}_in"
        else:
            wrapper += f",\n    input  logic [{w-1}:0]             {name}_in"
    for name, range_ in outputs:
        w = width_from_range(range_)
        if w == 1:
            wrapper += f",\n    output logic                     {name}_out"
        else:
            wrapper += f",\n    output logic [{w-1}:0]             {name}_out"
    wrapper += "\n);\n\n"

    # 声明输入信号线网
    for name, range_ in inputs:
        w = width_from_range(range_)
        if w == 1:
            wrapper += f"    logic {name};\n"
        else:
            wrapper += f"    logic [{w-1}:0] {name};\n"
    wrapper += "\n    // Output signals (connected to module)\n"
    for name, range_ in outputs:
        w = width_from_range(range_)
        if w == 1:
            wrapper += f"    logic {name};\n"
        else:
            wrapper += f"    logic [{w-1}:0] {name};\n"

    # 输入线网由顶层输入端口驱动
    wrapper += "\n    // Combinational connection from top-level input ports to module inputs\n"
    for name, range_ in inputs:
        wrapper += f"    assign {name} = {name}_in;\n"

    # 输出线网驱动顶层输出端口
    wrapper += "\n    // Combinational connection from module outputs to top-level output ports\n"
    for name, range_ in outputs:
        wrapper += f"    assign {name}_out = {name};\n"

    # 实例化原始模块
    wrapper += f"""
    // ------------------------------------------------------------
    // Instantiate original module
    // ------------------------------------------------------------
    {module_name} u_inst (
"""
    if hasclock:
        wrapper += f"""
		.clock (clk),
"""
    if hasclk:
        wrapper += f"""
		.clk (clk),
"""     
    if hasreset:
        wrapper += f"""
		.reset (~rst_n),
"""
    if hasrst:
        wrapper += f"""
		.rst (~rst_n),
"""
    for name, _ in inputs:
        wrapper += f"        .{name} ({name}),\n"
    for name, _ in outputs:
        wrapper += f"        .{name} ({name}),\n"
    wrapper = wrapper.rstrip(',\n') + "\n    );\n"

    wrapper += f"""
endmodule
"""
    return wrapper

def main():
    if len(sys.argv) not in (2, 3, 4):
        print("Usage: python auto_wrapper.py <source.v> [out_dir] [module_name]")
        sys.exit(1)
    print("auto_wrapper.py", sys.argv[1])
    src_file = sys.argv[1]
    src_path = Path(src_file)
    if not src_path.exists():
        print(f"File {src_file} not found.")
        sys.exit(1)
    module_name = sys.argv[3] if len(sys.argv) >= 4 else src_path.stem
    inputs, outputs, hasclock, hasclk, hasreset, hasrst = parse_ports(src_file, module_name)
    print(f"Found {len(inputs)} input ports, {len(outputs)} output ports")
    wrapper_code = generate_wrapper(inputs, outputs, hasclock, hasclk, hasreset, hasrst, module_name)
    if len(sys.argv) >= 3:
        out_dir = Path(sys.argv[2])
        out_dir.mkdir(parents=True, exist_ok=True)
    else:
        out_dir = src_path.parent
    out_file = out_dir / f"{module_name}_wrapper.sv"
    with open(out_file, 'w') as f:
        f.write(wrapper_code)
    print(f"Wrapper generated: {out_file}")
    # print(f"Address bits needed: { (len(inputs)+len(outputs)).bit_length() }")
    # print("Now you can use this wrapper as top-level to analyze critical path.")

if __name__ == "__main__":
    main()
