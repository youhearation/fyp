import requests
import json
import os
import time
import random
import urllib3
import numpy as np
from datetime import datetime

from shapely.geometry import shape, Point
from shapely.ops import unary_union, transform
from pyproj import Transformer

urllib3.disable_warnings()

# =====================================================
# 基础配置
# =====================================================
HEADERS = {
    'Host': 'yihe-api.slicejobs.com',
    'content-type': 'application/json',
    'User-Agent': 'Mozilla/5.0 (iPhone; CPU iPhone OS 18_5 like Mac OS X) '
                  'AppleWebKit/605.1.15 (KHTML, like Gecko) '
                  'Mobile/15E148 MicroMessenger/8.0.63 '
                  'NetType/WIFI Language/zh_CN',
    'Referer': 'https://servicewechat.com/wxedd07a0b2eb49cc4/89/page-frame.html',
}

# =====================================================
# 城市配置（区级 GeoJSON）
# =====================================================
CITIES = {
    "shanghai": {
        "geojson": "geo/shanghai.json",
        "grid_step_m": 3000,
        "query_radius_m": 4000
    },
    "suzhou": {
        "geojson": "geo/suzhou.json",
        "grid_step_m": 3500,
        "query_radius_m": 4500
    },
    "wuxi": {
        "geojson": "geo/wuxi.json",
        "grid_step_m": 3500,
        "query_radius_m": 4500
    }
}

# =====================================================
# 坐标系转换（经纬度 ↔ 米）
# =====================================================
to_m = Transformer.from_crs("EPSG:4326", "EPSG:3857", always_xy=True)
to_ll = Transformer.from_crs("EPSG:3857", "EPSG:4326", always_xy=True)

# =====================================================
# 安全请求
# =====================================================
def safe_post(url, headers, json_data, retries=3, timeout=20):
    for i in range(retries):
        try:
            r = requests.post(
                url,
                headers=headers,
                json=json_data,
                verify=False,
                timeout=timeout
            )
            return r.json()
        except Exception as e:
            print(f'[!] POST 失败 {i+1}/{retries}: {e}')
            time.sleep(2 + random.random() * 2)
    return None


def safe_get(url, headers, params=None, retries=3, timeout=20):
    for i in range(retries):
        try:
            r = requests.get(
                url,
                headers=headers,
                params=params,
                verify=False,
                timeout=timeout
            )
            return r.json()
        except Exception as e:
            print(f'[!] GET 失败 {i+1}/{retries}: {e}')
            time.sleep(2 + random.random() * 2)
    return None

# =====================================================
# GeoJSON → 市级 Polygon
# =====================================================
def load_city_polygon(geojson_path):
    with open(geojson_path, 'r', encoding='utf-8') as f:
        geo = json.load(f)

    polygons = []
    for feature in geo['features']:
        geom = shape(feature['geometry'])
        polygons.append(geom)

    return unary_union(polygons)

# =====================================================
# 生成行政区内网格点（米级精度）
# =====================================================
def generate_points_in_polygon(polygon, step_m):
    polygon_m = transform(to_m.transform, polygon)
    minx, miny, maxx, maxy = polygon_m.bounds

    xs = np.arange(minx, maxx, step_m)
    ys = np.arange(miny, maxy, step_m)

    points = []
    for x in xs:
        for y in ys:
            p = Point(x, y)
            if polygon_m.contains(p):
                lon, lat = to_ll.transform(x, y)
                points.append((round(lon, 6), round(lat, 6)))

    return points

# =====================================================
# 接口封装
# =====================================================
def fetch_list(lon, lat, distance, page):
    url = 'https://yihe-api.slicejobs.com/app/product/map_query'
    payload = {
        "longitude": lon,
        "latitude": lat,
        "distance": distance,
        "current_page": page,
        "per_page": 20
    }
    return safe_post(url, HEADERS, payload)


def fetch_detail(pid):
    url = f'https://yihe-api.slicejobs.com/app/product/get_{pid}'
    params = {
        "id": pid,
        "map_query": 0
    }
    return safe_get(url, HEADERS, params)

# =====================================================
# 保存 JSON
# =====================================================
def save_json(data, path):
    os.makedirs(os.path.dirname(path), exist_ok=True)
    with open(path, 'w', encoding='utf-8') as f:
        json.dump(data, f, ensure_ascii=False, indent=2)

# =====================================================
# 主流程
# =====================================================
def run():
    timestamp = datetime.now().strftime('%Y%m%d_%H%M')

    for city, cfg in CITIES.items():
        print(f'\n========== 开始抓取 {city} ==========')

        city_polygon = load_city_polygon(cfg['geojson'])
        points = generate_points_in_polygon(
            city_polygon,
            cfg['grid_step_m']
        )

        print(f'[+] {city} 扫描点数量: {len(points)}')

        folder = f'data/{city}/{timestamp}'
        all_items = {}

        # ---------- 列表 ----------
        for idx, (lon, lat) in enumerate(points, 1):
            print(f'[+] {city} 点 {idx}/{len(points)} {lon},{lat}')
            page = 1

            while True:
                data = fetch_list(
                    lon,
                    lat,
                    cfg['query_radius_m'],
                    page
                )
                if not data:
                    break

                items = data.get('detail', {}).get('data', [])
                if not items:
                    break

                for item in items:
                    pid = item.get('id')
                    ilon = item.get('longitude')
                    ilat = item.get('latitude')

                    if not pid or not ilon or not ilat:
                        continue

                    # 二次校验：必须在行政区内
                    if not city_polygon.contains(Point(ilon, ilat)):
                        continue

                    all_items[pid] = item

                page += 1
                time.sleep(1.2)

        save_json(
            list(all_items.values()),
            f'{folder}/product_list.json'
        )

        # ---------- 详情 ----------
        for pid in all_items:
            print(f'[+] {city} 详情 {pid}')
            detail = fetch_detail(pid)
            if detail:
                save_json(
                    detail,
                    f'{folder}/product_{pid}.json'
                )
            time.sleep(1.2)

        print(f'[✓] {city} 完成，共 {len(all_items)} 条')

    print('\n[✓] 所有城市抓取完成')

# =====================================================
# 程序入口
# =====================================================
if __name__ == '__main__':
    run()
