import argparse
import sys
import subprocess
from pathlib import Path

lib = "/tools/nangate45/NangateOpenCellLibrary_typical.lib"

def parse_arguments():
    parser = argparse.ArgumentParser()
    parser.add_argument("-reg", action = "store_true", help = "使用寄存器作为 top module 外层包装")
    parser.add_argument("filenames", nargs = "+", help = "模块文件名，第一个模块文件为 top module")

    if len(sys.argv) == 1:
        parser.print_help()
        sys.exit(0)

    return parser.parse_args()

def main():
    args = parse_arguments()
    print("top module: " + args.filenames[0])
    print("""
-------------------------------------------------------------------------------
          Top module wrapper generate
-------------------------------------------------------------------------------
""")
    if args.reg:
        subprocess.run(["python3", "auto_reg_wrapper.py", args.filenames[0]])
    else:
        subprocess.run(["python3", "auto_wrapper.py", args.filenames[0]])

    print("""
-------------------------------------------------------------------------------
          Yosys batch generate
-------------------------------------------------------------------------------
""")

    src_path = Path(args.filenames[0]).with_suffix('')
    if args.reg:
        wrapper_file = f"{src_path}_reg_wrapper.sv"
    else:
        wrapper_file = f"{src_path}_wrapper.sv"
    yosys_batch = f"""read_verilog -sv {wrapper_file}\n"""
    
    for file in args.filenames:
        if Path(file).suffix == ".sv":
            yosys_batch = yosys_batch + f"read_verilog -sv {file}\n"
        else:
            yosys_batch = yosys_batch + f"read_verilog {file}\n"
    
    yosys_batch = yosys_batch + f"""hierarchy -check -auto-top
proc; opt; fsm; opt; memory; opt
synth -flatten
dfflibmap -liberty {lib}
abc -liberty {lib}
write_verilog -noattr synthesized_netlist.v"""
    
    with open("run.ys", "w") as file:
        file.write(yosys_batch)

    print(yosys_batch)

    print("""
-------------------------------------------------------------------------------
          Yosys running......
-------------------------------------------------------------------------------
""")
    
    print("yosys run.ys")
    
    subprocess.run(["yosys", "run.ys"])

if __name__ == "__main__":
    main()