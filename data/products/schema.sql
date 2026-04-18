-- 扫地机器人产品数据库 schema (适配 Coze 数据格式)
-- 创建产品表
CREATE TABLE IF NOT EXISTS products (
    id INTEGER PRIMARY KEY AUTOINCREMENT,

    -- 基本信息（Coze 返回的核心字段）
    brand VARCHAR(50) NOT NULL,           -- 品牌（如：石头、科沃斯、小米）
    model VARCHAR(100) NOT NULL,          -- 型号（如：P10 Pro、T20 Pro）
    price INTEGER,                        -- 价格（元）

    -- 核心性能参数
    suction_power INTEGER,                -- 吸力（Pa）
    battery_life INTEGER,                 -- 续航时间（分钟）
    cleaning_area INTEGER,                -- 适用面积（㎡）
    navigation_type VARCHAR(50),          -- 导航类型（LDS激光、视觉导航等）

    -- 功能特性
    mopping_function BOOLEAN,             -- 拖地功能（支持/不支持）
    self_charging BOOLEAN,                -- 自动回充（支持/不支持）
    carpet_recognition BOOLEAN,           -- 地毯识别（Coze 常用）
    auto_mop_lifting BOOLEAN,             -- 拖布自动抬升（Coze 常用）

    -- 其他特性（可选）
    dustbin_capacity REAL,                -- 尘盒容量（L）
    water_tank_capacity INTEGER,          -- 水箱容量（mL）
    virtual_wall BOOLEAN,                 -- 虚拟墙
    app_control BOOLEAN,                  -- APP 控制
    voice_control BOOLEAN,                -- 语音控制
    noise_level INTEGER,                  -- 噪音等级（dB）
    rating REAL,                          -- 评分（1-5）
    review_count INTEGER,                 -- 评价数量

    -- 产品规格（可选）
    weight REAL,                          -- 重量（kg）
    height REAL,                          -- 高度（cm）
    release_year INTEGER,                 -- 上市年份

    -- Coze 特有字段
    description TEXT,                     -- 产品描述/推荐理由
    pros TEXT,                            -- 优点/为什么适合
    cons TEXT,                            -- 缺点/需要注意
    suitable_for TEXT,                    -- 适用人群/场景

    -- 数据来源和状态
    source VARCHAR(50) DEFAULT 'manual',  -- 来源：coze、manual、imported
    source_id VARCHAR(100),               -- 原始来源ID（如Coze消息ID）
    is_active BOOLEAN DEFAULT 1,          -- 是否有效

    -- 时间戳
    created_at TIMESTAMP DEFAULT CURRENT_TIMESTAMP,
    updated_at TIMESTAMP DEFAULT CURRENT_TIMESTAMP
);

-- 创建索引以提高查询性能
CREATE INDEX IF NOT EXISTS idx_products_brand ON products(brand);
CREATE INDEX IF NOT EXISTS idx_products_price ON products(price);
CREATE INDEX IF NOT EXISTS idx_products_suction ON products(suction_power);
CREATE INDEX IF NOT EXISTS idx_products_rating ON products(rating);
CREATE INDEX IF NOT EXISTS idx_products_source ON products(source);
CREATE INDEX IF NOT EXISTS idx_products_active ON products(is_active);

-- 创建品牌表（用于品牌管理和标准化）
CREATE TABLE IF NOT EXISTS brands (
    id INTEGER PRIMARY KEY AUTOINCREMENT,
    name VARCHAR(50) NOT NULL UNIQUE,     -- 品牌名称
    name_en VARCHAR(50),                  -- 英文名称
    logo_url VARCHAR(255),                -- Logo URL
    country VARCHAR(50),                  -- 所属国家
    website VARCHAR(255),                 -- 官网
    is_active BOOLEAN DEFAULT 1,
    created_at TIMESTAMP DEFAULT CURRENT_TIMESTAMP
);

-- 插入常见品牌
INSERT OR IGNORE INTO brands (name, name_en, country) VALUES
('石头', 'Roborock', '中国'),
('科沃斯', 'ECOVACS', '中国'),
('小米', 'Xiaomi', '中国'),
('米家', 'MiJia', '中国'),
('云鲸', 'Narwal', '中国'),
('追觅', 'Dreame', '中国'),
('美的', 'Midea', '中国'),
('海尔', 'Haier', '中国'),
('360', '360', '中国'),
('iRobot', 'iRobot', '美国'),
('戴森', 'Dyson', '英国'),
('飞利浦', 'Philips', '荷兰'),
('松下', 'Panasonic', '日本'),
('浦桑尼克', 'Proscenic', '中国'),
('Neato', 'Neato', '美国');

-- 创建导入日志表（用于追踪 Coze 数据导入）
CREATE TABLE IF NOT EXISTS import_logs (
    id INTEGER PRIMARY KEY AUTOINCREMENT,
    source VARCHAR(50) NOT NULL,          -- 来源（coze、file等）
    source_identifier VARCHAR(255),       -- 来源标识（如文件名、消息ID）
    total_count INTEGER DEFAULT 0,        -- 总记录数
    inserted_count INTEGER DEFAULT 0,     -- 插入数
    skipped_count INTEGER DEFAULT 0,      -- 跳过数（重复）
    error_count INTEGER DEFAULT 0,        -- 错误数
    import_data TEXT,                     -- 导入的原始数据（JSON）
    created_at TIMESTAMP DEFAULT CURRENT_TIMESTAMP
);

-- 创建更新触发器（自动更新 updated_at）
CREATE TRIGGER IF NOT EXISTS update_products_timestamp
AFTER UPDATE ON products
BEGIN
    UPDATE products SET updated_at = CURRENT_TIMESTAMP WHERE id = NEW.id;
END;