"""用政府開放資料「新北市門牌位置數值資料.csv」建立離線門牌索引。

  python build_geo_index.py "C:\\路徑\\新北市門牌位置數值資料.csv"

產生 data/ntpc_doorplate_index.npz 與 data/ntpc_prefix_index.json.gz（合計約數十 MB），
之後 app 不再需要原始 CSV，也不會連到任何外部地圖服務。
"""
import sys
from address_geocoder import build_index

if __name__ == '__main__':
    if len(sys.argv) != 2:
        raise SystemExit(__doc__)
    print(build_index(sys.argv[1]))
