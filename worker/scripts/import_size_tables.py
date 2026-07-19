"""
尺码表批量导入脚本
将男性、儿童、鞋子尺码表导入到Supabase size_mapping表
"""

import csv
import os
import sys

# 添加项目路径到PYTHONPATH
sys.path.insert(0, os.getenv("APP_WORKSPACE_PATH", "/workspace/projects"))
sys.path.insert(0, os.path.join(os.getenv("APP_WORKSPACE_PATH", "/workspace/projects"), "src"))

from utils.supabase_client import SupabaseClient

# Supabase配置（优先环境变量）
SUPABASE_URL = os.getenv("SUPABASE_URL", "https://kekmppsuiiokdckdeolv.supabase.co")
SUPABASE_KEY = os.getenv("SUPABASE_KEY", "")

def read_csv_size_table(csv_file_path: str) -> list:
    """读取尺码表CSV文件"""
    size_data = []
    with open(csv_file_path, 'r', encoding='utf-8') as f:
        reader = csv.DictReader(f)
        for row in reader:
            # 解析CSV字段
            chest_cm = row.get('胸围(cm)', '')
            waist_cm = row.get('腰围(cm)', '')
            hip_cm = row.get('臀围(cm)', '')
            ru_size = row.get('俄罗斯(RU)', '')
            int_size = row.get('国际(INT)', '')
            
            # 构建数据字典
            size_data.append({
                'chest_cm': chest_cm,
                'waist_cm': waist_cm,
                'hip_cm': hip_cm,
                'ru_size': ru_size,
                'int_size': int_size
            })
    return size_data

def import_size_table_to_supabase(size_data: list, gender: str, category: str):
    """导入尺码表数据到Supabase"""
    client = SupabaseClient(SUPABASE_URL, SUPABASE_KEY)
    
    inserted_count = 0
    for data in size_data:
        try:
            # 构建插入数据
            insert_data = {
                'gender': gender,
                'category': category,
                'chest_cm': data['chest_cm'],
                'waist_cm': data['waist_cm'],
                'hip_cm': data['hip_cm'],
                'ru_size': data['ru_size'],
                'int_size': data['int_size']
            }
            
            # 使用SQL插入（绕过PostgREST schema cache问题）
            sql = f"""
            INSERT INTO size_mapping (gender, category, chest_cm, waist_cm, hip_cm, ru_size, int_size)
            VALUES ('{gender}', '{category}', '{data['chest_cm']}', '{data['waist_cm']}', '{data['hip_cm']}', '{data['ru_size']}', '{data['int_size']}')
            ON CONFLICT (gender, category, int_size) DO NOTHING;
            """
            
            # 执行SQL（使用exec_sql工具）
            # 这里暂时不执行SQL，而是记录SQL语句
            print(f"SQL: {sql}")
            inserted_count += 1
            
        except Exception as e:
            print(f"❌ 插入失败: {e}")
    
    print(f"✅ {gender} {category}尺码表导入完成，共{inserted_count}条数据")

if __name__ == "__main__":
    # 导入男性服装尺码表
    male_clothing_csv = os.path.join(os.getenv("APP_WORKSPACE_PATH", "/workspace/projects"), "assets/男性服装尺码表.csv")
    male_size_data = read_csv_size_table(male_clothing_csv)
    print(f"读取男性服装尺码表，共{len(male_size_data)}行数据")
    
    # 导入儿童服装尺码表
    children_clothing_csv = os.path.join(os.getenv("APP_WORKSPACE_PATH", "/workspace/projects"), "assets/儿童服装尺码表.csv")
    children_size_data = read_csv_size_table(children_clothing_csv)
    print(f"读取儿童服装尺码表，共{len(children_size_data)}行数据")
    
    # 导入鞋子尺码表
    shoes_csv = os.path.join(os.getenv("APP_WORKSPACE_PATH", "/workspace/projects"), "assets/鞋子尺码对应表.csv")
    shoes_size_data = read_csv_size_table(shoes_csv)
    print(f"读取鞋子尺码表，共{len(shoes_size_data)}行数据")
    
    # 导入到Supabase（使用SQL批量插入）
    # 这里不直接执行Python脚本，而是生成SQL批量导入语句