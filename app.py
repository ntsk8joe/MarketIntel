import argparse
from pathlib import Path
from marketintel.server import serve


if __name__ == '__main__':
    parser = argparse.ArgumentParser(description='MarketIntel 本地情报与决策工作台')
    parser.add_argument('--port', type=int, default=8765)
    parser.add_argument('--data-root', default=str(Path(__file__).resolve().parent))
    parser.add_argument('--demo', action='store_true', help='显式导入历史演示方向；不会自动采集')
    args = parser.parse_args()
    server = serve(args.data_root, args.port, load_demo=args.demo)
    print('MarketIntel 已启动：http://127.0.0.1:' + str(server.server_port), flush=True)
    print('Obsidian 知识库：' + str(server.store.vault), flush=True)
    try:
        server.serve_forever()
    except KeyboardInterrupt:
        print('\n已停止；数据保存在本地。', flush=True)
    finally:
        server.server_close()
