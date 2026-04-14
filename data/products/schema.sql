-- 扫地机器人推荐系统数据库 schema
-- 创建产品表
CREATE TABLE IF NOT EXISTS products (
    id INTEGER PRIMARY KEY AUTOINCREMENT,
    brand VARCHAR(50) NOT NULL,  -- 品牌
    model VARCHAR(100) NOT NULL,  -- 型号
    suction_power INTEGER NOT NULL,  -- 吸力 (Pa)
    navigation_type VARCHAR(50) NOT NULL,  -- 导航类型
    battery_life INTEGER NOT NULL,  -- 续航时间 (分钟)
    dustbin_capacity REAL NOT NULL,  -- 尘盒容量 (L)
    water_tank_capacity REAL NOT NULL,  -- 水箱容量 (mL)
    mopping_function BOOLEAN NOT NULL,  -- 拖地功能
    self_charging BOOLEAN NOT NULL,  -- 自动回充
    virtual_wall BOOLEAN NOT NULL,  -- 虚拟墙
    app_control BOOLEAN NOT NULL,  -- APP 控制
    voice_control BOOLEAN NOT NULL,  -- 语音控制
    noise_level INTEGER NOT NULL,  -- 噪音等级 (dB)
    cleaning_area INTEGER NOT NULL,  -- 适用面积 (㎡)
    price REAL NOT NULL,  -- 价格 (元)
    rating REAL NOT NULL,  -- 评分 (1-5)
    review_count INTEGER NOT NULL,  -- 评价数量
    weight REAL NOT NULL,  -- 重量 (kg)
    height REAL NOT NULL,  -- 高度 (cm)
    release_year INTEGER NOT NULL,  -- 上市年份
    created_at TIMESTAMP DEFAULT CURRENT_TIMESTAMP,
    updated_at TIMESTAMP DEFAULT CURRENT_TIMESTAMP
);

-- 创建索引以提高查询性能
CREATE INDEX IF NOT EXISTS idx_products_brand ON products(brand);
CREATE INDEX IF NOT EXISTS idx_products_suction ON products(suction_power);
CREATE INDEX IF NOT EXISTS idx_products_price ON products(price);
CREATE INDEX IF NOT EXISTS idx_products_rating ON products(rating);
