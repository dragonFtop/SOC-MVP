from tool import *
from datetime import datetime
import os

def main():
    # 生成时间戳
    timestamp = datetime.now().strftime("%Y%m%d_%H%M%S")
    
    # 创建以时间戳命名的输出目录
    output_dir = f"outputs/{timestamp}"
    os.makedirs(output_dir, exist_ok=True)
    
    # 依次执行各个模块，传入时间戳
    build_evidence(timestamp=timestamp)
    calculate_readiness(timestamp=timestamp)
    analyze_event(timestamp=timestamp)
    verify(timestamp=timestamp)
    load_to_opensearch(timestamp=timestamp)
    generate_report(timestamp=timestamp)

if __name__ == "__main__":
    main()