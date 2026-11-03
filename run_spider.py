import os
import sys
import runpy

base_dir = os.path.dirname(os.path.abspath(__file__))
os.chdir(base_dir)

main_script = os.path.join(base_dir, "课件爬虫.py")
if not os.path.exists(main_script):
    input("找不到主程序 课件爬虫.py，请按回车键退出...")
    sys.exit(1)

runpy.run_path(main_script, run_name="__main__")
