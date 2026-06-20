# ============================================================
# 配置文件 — 修改这里来调整爬取行为和相关度判断
# ============================================================

import os

# Anthropic API Key（从 https://console.anthropic.com 获取）
ANTHROPIC_API_KEY = os.getenv("ANTHROPIC_API_KEY", "")

# ============================================================
# LLM 接入配置
# ============================================================

# auto: 优先 DeepSeek API，失败后回退 Claude Code CLI
# deepseek: 只用 DeepSeek API
# claude: 只用 Claude Code CLI
LLM_PROVIDER = os.getenv("LLM_PROVIDER", "auto").lower()

# DeepSeek API 使用 OpenAI 兼容接口。默认用非思考模式的 v4-flash 做轻量文本处理。
DEEPSEEK_API_KEY = os.getenv("DEEPSEEK_API_KEY", "")
DEEPSEEK_BASE_URL = os.getenv("DEEPSEEK_BASE_URL", "https://api.deepseek.com")
DEEPSEEK_MODEL_FAST = os.getenv("DEEPSEEK_MODEL_FAST", "deepseek-v4-flash")
DEEPSEEK_MODEL_STRONG = os.getenv("DEEPSEEK_MODEL_STRONG", "deepseek-v4-flash")

# Claude Code CLI 回退模型
CLAUDE_HAIKU_MODEL = os.getenv("CLAUDE_HAIKU_MODEL", "claude-haiku-4-5-20251001")
CLAUDE_SONNET_MODEL = os.getenv("CLAUDE_SONNET_MODEL", "claude-sonnet-4-6")

# 批量评分时每个 LLM 请求包含的文章数。调小更稳，调大更省调用次数。
LLM_SCORING_BATCH_SIZE = int(os.getenv("LLM_SCORING_BATCH_SIZE", "40"))

# ============================================================
# 信源层级定义
# ============================================================

SOURCE_TIER_LABELS = {
    "official":   "官方公告",
    "regulatory": "监管披露",
    "industry":   "行业组织",
    "academic":   "会议论文",
    "media":      "行业媒体",
    "aggregator": "聚合转载",
}

# 去重时层级优先级（数值越小越优先保留）
TIER_PRIORITY = {t: i for i, t in enumerate(
    ["official", "regulatory", "industry", "academic", "media", "aggregator"]
)}

# ============================================================
# 信息源配置
# ============================================================

# RSS 订阅源（自动解析，优先使用）
# weight 权重倍率影响最终排名；tier 决定去重优先级和报告标注
RSS_SOURCES = [
    # ── 行业媒体（二手/深度解读层）────────────────────────────────────
    {
        "name": "EE Times",
        "url": "https://www.eetimes.com/feed/",
        "language": "en",
        "weight": 1.0,
        "tier": "media",
    },
    {
        "name": "Semiconductor Engineering",
        "url": "https://semiengineering.com/feed/",
        "language": "en",
        "weight": 1.1,
        "tier": "media",
    },
    {
        "name": "IEEE Spectrum",
        "url": "https://spectrum.ieee.org/feeds/feed.rss",
        "language": "en",
        "weight": 1.0,
        "tier": "media",
    },
    {
        "name": "The Next Platform",
        "url": "https://www.nextplatform.com/feed/",
        "language": "en",
        "weight": 0.9,
        "tier": "media",
    },
    {
        "name": "EDN Network",
        "url": "https://www.edn.com/feed/",
        "language": "en",
        "weight": 0.9,
        "tier": "media",
    },
    {
        "name": "Electronic Design",
        "url": "https://www.electronicdesign.com/__rss/website-scheduled-content.xml?input=%7B%22sectionAlias%22:%22home%22%7D",
        "language": "en",
        "weight": 0.9,
        "tier": "media",
        "enabled": False,   # 403 Forbidden
    },
    # ── 公司官方新闻室 / IR（一手源，高权重）────────────────────────────
    {
        "name": "TSMC 新闻中心",
        "url": "https://pr.tsmc.com/english/news/rss",
        "language": "en",
        "weight": 1.8,
        "tier": "official",
        "enabled": False,   # 无公开 RSS，403
    },
    {
        "name": "NVIDIA 新闻室",
        "url": "https://nvidianews.nvidia.com/cats/press_release.xml",
        "language": "en",
        "weight": 1.7,
        "tier": "official",
    },
    {
        "name": "Intel Newsroom",
        "url": "https://newsroom.intel.com/feed/",
        "language": "en",
        "weight": 1.7,
        "tier": "official",
        # ConnectionResetError — 疑似 GFW，VPN 环境下可能恢复
    },
    {
        "name": "AMD 新闻稿",
        "url": "https://ir.amd.com/rss/news-releases.xml",
        "language": "en",
        "weight": 1.6,
        "tier": "official",
    },
    {
        "name": "ASML 新闻稿",
        "url": "https://www.asml.com/en/news/press-releases/rss",
        "language": "en",
        "weight": 1.7,
        "tier": "official",
        "enabled": False,   # 无公开 RSS，ConnectionResetError
    },
    {
        "name": "Micron Newsroom",
        "url": "https://investors.micron.com/rss/news-releases.xml",
        "language": "en",
        "weight": 1.5,
        "tier": "official",
    },
    {
        "name": "Samsung Semiconductor",
        "url": "https://news.samsungsemiconductor.com/global/feed/",
        "language": "en",
        "weight": 1.5,
        "tier": "official",
    },
    {
        "name": "SK hynix 新闻",
        "url": "https://news.skhynix.com/feed/",
        "language": "en",
        "weight": 1.5,
        "tier": "official",
        # ConnectionResetError — URL 有效，疑似 GFW，VPN 环境下可恢复
    },
    {
        "name": "Synopsys 新闻稿",
        "url": "https://news.synopsys.com/home?pagetemplate=rss",
        "language": "en",
        "weight": 1.4,
        "tier": "official",
    },
    {
        "name": "Broadcom 新闻稿",
        "url": "https://investors.broadcom.com/rss/news-releases.xml",
        "language": "en",
        "weight": 1.4,
        "tier": "official",
    },
    {
        "name": "Qualcomm 新闻稿",
        "url": "https://www.qualcomm.com/news/releases.rss",
        "language": "en",
        "weight": 1.4,
        "tier": "official",
        "enabled": False,   # 404，无有效 RSS 路径
    },
    # ── 行业组织（一手源）──────────────────────────────────────────────
    {
        "name": "SEMI 新闻稿",
        "url": "https://www.semi.org/en/rss",
        "language": "en",
        "weight": 1.3,
        "tier": "industry",
        "enabled": False,   # 404/403
    },
    # SIA 无 RSS，改用 HTML_SOURCES 抓取首页新闻列表
    # ── 会议/论文（早期技术信号）────────────────────────────────────────
    # SEC EDGAR / 上交所 / 巨潮 / BIS 需要额外过滤或鉴权，暂用 HTML 爬虫或后续专项接入。
    {
        "name": "arXiv cs.AR",
        "url": "https://arxiv.org/rss/cs.AR",
        "language": "en",
        "weight": 1.2,
        "tier": "academic",
        "max_items": 15,   # arXiv 每日条目多，限制拉取量
    },
]

# HTML 爬取站点（RSS 不可用时使用传统爬虫）
HTML_SOURCES = [
    {
        "name": "ICsmart",
        "url": "http://www.icsmart.cn",
        "language": "zh",
        "weight": 1.0,
        "tier": "aggregator",
        "enabled": False,   # 暂时停用
        "index_urls": [
            "http://www.icsmart.cn",
            "http://www.icsmart.cn/news",
        ],
        "article_url_pattern": r"icsmart\.cn/.+",
        "skip_url_keywords": ["tag", "category", "page", "author", "login", "register"],
    },
    {
        "name": "SIA 新闻",
        "url": "https://www.semiconductors.org",
        "language": "en",
        "weight": 1.3,
        "tier": "industry",
        "index_urls": ["https://www.semiconductors.org/"],
        "article_url_pattern": r"semiconductors\.org/[a-z0-9][a-z0-9-]+/$",
        "skip_url_keywords": [
            "category", "tag", "page", "author", "about", "contact",
            "privacy", "member", "semis-101", "policy", "market-data",
            "news-events", "wsts", "supply-chain", "map-of",
            "jobs", "login", "events", "map", "subscribe",
        ],
    },
    {
        "name": "爱集微",
        "url": "https://aijiweinews.com",
        "language": "zh",
        "weight": 1.0,
        "tier": "aggregator",
        "index_urls": [
            "https://aijiweinews.com",
            "https://aijiweinews.com/news",
        ],
        "article_url_pattern": r"aijiweinews\.com/.+",
        "skip_url_keywords": ["tag", "category", "page", "author", "login", "user", "search"],
    },
]

# ============================================================
# 采集与分析参数
# ============================================================

# 每个 HTML 站点最多爬取的候选文章数
MAX_CANDIDATES_PER_SITE = 30

# 每个 RSS 源最多取的条目数
MAX_RSS_ITEMS_PER_SOURCE = 20

# 爬取文本的字符上限（采集与 LLM 摘要共享此上限）
CRAWL_TEXT_LIMIT = 2000
# RSS 文章文本短于此字符数时，补抓原文页以获取全文和图片（复用同一 HTTP 请求）
CRAWL_ENRICH_MIN_TEXT = 400

# 文章时效窗口（RSS 源有日期时生效）：技术版每周跑、商业版每天跑
TECH_ARTICLE_AGE_DAYS = 7
BUSINESS_ARTICLE_AGE_DAYS = 2   # 留 1 天容错，跨天重复靠 seen 记录排除

# 技术版（周报）精选文章数
TECH_TOP_N = 15

# 商业版（日报）不限条目，评分达到该阈值即收录
BUSINESS_MIN_SCORE = 4

# 已报道 URL 历史记录上限（output/seen_<slug>.json，防止文件无限增长）
SEEN_URLS_KEEP = 5000

# 标题相似度阈值（0~1），超过则视为重复，保留评分更高的
DEDUP_SIMILARITY_THRESHOLD = 0.72

# 兼容旧命名
MAX_ARTICLE_AGE_DAYS = TECH_ARTICLE_AGE_DAYS
TOP_N = TECH_TOP_N

# ============================================================
# 文章分类（Claude 会自动打标签）
# ============================================================

# 技术版报告的固定门类
TECH_CATEGORIES = [
    "制造&工艺",    # 制程、晶圆制造、设备材料、先进封装、测试、良率
    "芯片架构",    # CPU/GPU/NPU/ASIC、RISC-V、Chiplet架构、异构计算
    "EDA工具",     # EDA工具、验证仿真、AI辅助设计、设计自动化
    "标准&会议",   # PCIe、UCIe、CXL、HBM等标准，以及ISSCC/DAC/IEDM等会议
    "其他技术",
]

# 商业版不做固定分类分组，用标签帮助读者快速识别主题
BUSINESS_TAGS = [
    "财报业绩",     # 营收、利润、毛利率、业务线增长、销售指引、股价反应
    "融资并购",     # 融资、IPO、并购、资产重组、战略投资、产业基金
    "产能供应链",   # 扩产、晶圆厂、封装产能、HBM/DRAM供应、设备交付、锁单
    "政策管制",     # 出口管制、补贴、关税、产业政策、地缘政治、合规风险
    "客户订单",     # 大客户合作、长期订单、云厂商/车企/AI公司采购、验证进展
    "市场价格",     # 市场规模、价格周期、库存、市场份额、机构预测
    "公司战略",     # 路线图、组织调整、生态合作、业务转型、管理层表态
    "资本市场",
    "其他商业",
]

# 兼容 reporter.py 旧入口；技术版仍按这些类别分组。
CATEGORIES = TECH_CATEGORIES

# ============================================================
# 规则兜底关键词桶（无 API Key / LLM 失败时用于评分与分类）
# ------------------------------------------------------------
# analyzer.py 的 _fallback_tech_score / _fallback_business_tags 从这里读取；
# GUI「关键词词库」编辑的就是这两份结构（settings_store 覆盖层可整体替换）。
# ============================================================

TECH_KEYWORD_BUCKETS = {
    "制造&工艺": (
        "process", "node", "nm", "gaa", "nanosheet", "euv", "fab", "foundry",
        "packaging", "advanced packaging", "cowos", "copos", "hbm", "yield",
        "wafer", "test", "metrology", "封装", "制程", "工艺", "晶圆", "良率",
        "设备", "材料", "测试",
    ),
    "芯片架构": (
        "cpu", "gpu", "npu", "asic", "risc-v", "chiplet", "accelerator",
        "architecture", "nvlink", "processor", "memory architecture",
        "cpo", "npo", "oio", "optical i/o", "optical io", "optical interconnect",
        "silicon photonics", "photonics", "co-packaged optics", "near-package optics",
        "optical engine", "photonic integrated circuit", "pic", "lpo", "linear pluggable optics",
        "optical module", "optical transceiver", "coherent optics", "pam4",
        "架构", "异构", "处理器", "加速器", "算力", "硅光", "光互连",
        "光i/o", "光io", "光电共封装", "共封装光学", "光引擎", "光模块",
        "光收发器", "光通信", "光通讯", "相干光",
    ),
    "EDA工具": (
        "eda", "synopsys", "cadence", "siemens", "verification", "simulation",
        "place and route", "dft", "ip", "design automation", "agentic",
        "验证", "仿真", "布局布线", "设计自动化",
    ),
    "标准&会议": (
        "pcie", "ucie", "cxl", "ethernet", "standard", "isscc", "dac",
        "iedm", "hot chips", "computex", "conference", "symposium",
        "标准", "会议", "论坛", "大会",
    ),
}

BUSINESS_KEYWORD_BUCKETS = {
    "财报业绩": ("revenue", "earnings", "margin", "guidance", "profit", "sales", "财报", "营收", "利润", "指引", "业绩"),
    "融资并购": ("funding", "ipo", "acquisition", "merger", "invest", "融资", "并购", "上市", "投资"),
    "产能供应链": (
        "capacity", "supply", "fab", "shipment", "wafer", "shortage",
        "cpo", "npo", "oio", "silicon photonics", "optical module",
        "optical transceiver", "co-packaged optics", "lpo",
        "产能", "供应链", "扩产", "出货", "晶圆厂", "硅光", "光模块",
        "光收发器", "光通信", "光通讯", "光互连", "光电共封装",
    ),
    "政策管制": ("export control", "tariff", "policy", "subsidy", "chips act", "管制", "政策", "补贴", "关税"),
    "客户订单": (
        "customer", "order", "contract", "deal", "meta", "google", "nvidia", "amd",
        "cpo", "npo", "oio", "optical module", "optical transceiver",
        "客户", "订单", "合作", "硅光", "光模块", "光收发器", "光互连",
    ),
    "市场价格": (
        "market", "price", "share", "forecast", "inventory", "optical module",
        "silicon photonics", "市场", "价格", "份额", "预测", "库存", "硅光",
        "光模块", "光通信", "光通讯",
    ),
    "公司战略": ("roadmap", "strategy", "partnership", "ecosystem", "战略", "路线图", "生态", "转型"),
    "资本市场": ("stock", "shares", "sell-off", "rally", "valuation", "股价", "市值", "抛售", "反弹"),
}

# ============================================================
# Prompt 配置
# ============================================================

TECH_RELEVANCE_PROMPT = """
你是半导体技术资讯筛选助手。请判断以下文章是否适合进入「技术版周报」，给出0-10分的技术相关度评分，并从技术分类中选择最匹配的一类。

技术分类：制造&工艺 | 芯片架构 | EDA工具 | 标准&会议 | 其他技术

重点关注（高分）：
- 制造&工艺：先进制程、晶圆制造、设备材料、良率、测试、先进封装、HBM封装、CoWoS/CoPoS、背面供电、GAA、High-NA EUV等。
- 芯片架构：CPU/GPU/NPU/ASIC、RISC-V、Chiplet架构、异构计算、AI加速器、存储架构、互连架构、系统级算力架构、硅光/光互连/光I/O、CPO/NPO/OIO、光电共封装等。
- EDA工具：EDA软件、验证仿真、布局布线、DFT、IP、AI辅助设计、Agentic EDA、设计到制造协同。
- 标准&会议：PCIe、UCIe、CXL、HBM、Ethernet、NVLink、800G/1.6T、PAM4/相干光通信等标准进展，以及DAC、ISSCC、IEDM、Hot Chips、OFC、Computex等重要会议中的技术发布。

排除/低分：
- 纯财报、股价、融资、并购、客户订单，除非包含实质技术细节。
- 产能扩充/量产计划/扩产时间表，除非文章包含具体新工艺节点或技术路线细节（纯产能商业新闻评分≤3，并归入"其他技术"或直接不选）。
- 泛AI、泛消费电子、泛宏观经济，未落到芯片技术。

请严格按照以下JSON格式返回，不要有任何额外文字：
{"score": <0-10的整数>, "category": "<分类名>", "reason": "<简短理由，15字以内>"}
"""

BUSINESS_RELEVANCE_PROMPT = """
你是半导体产业与公司动态分析助手。请判断以下文章是否适合进入「商业版周报」，给出0-10分的商业相关度评分，并给出2-4个标签。

标签候选：财报业绩 | 融资并购 | 产能供应链 | 政策管制 | 客户订单 | 市场价格 | 公司战略 | 资本市场 | 其他商业

重点关注（高分）：
- 财报业绩：营收、利润、毛利率、业务线增长、销售指引、股价或市场反应。
- 融资并购：融资、IPO、并购、资产重组、战略投资、产业基金。
- 产能供应链：晶圆厂扩产、封装产能、HBM/DRAM/NAND供应、设备交付、产能锁定、供应短缺。
- 政策管制：出口管制、政府补贴、关税、产业政策、地缘政治、合规风险。
- 客户订单：大客户合作、长期订单、云厂商采购、车企/AI公司design win、客户验证进展，以及AI数据中心光模块、CPO/NPO/OIO、硅光/光互连相关订单。
- 市场价格：全球/区域市场规模、价格周期、库存变化、市场份额、机构预测，含光模块、硅光、光通信器件等细分市场。
- 公司战略：技术路线图背后的商业策略、组织调整、生态合作、业务转型、管理层表态。

排除/低分：
- 纯技术论文、工艺细节、EDA工具功能介绍，除非有明确商业影响。
- 泛科技新闻、无半导体产业链关系的商业消息。

请严格按照以下JSON格式返回，不要有任何额外文字：
{"score": <0-10的整数>, "tags": "<标签1,标签2>", "reason": "<简短理由，15字以内>"}
"""

TRANSLATION_PROMPT = """
你是半导体行业资讯翻译助手。请将以下英文文章的标题与正文摘录翻译成中文。

翻译要求：
1. 标题：忠实原意，不超过40字；公司名、芯片型号、技术缩写（EUV、GAA、HBM、CoWoS、PCIe、CXL、UCIe、RISC-V、NVLink 等）可保留英文
2. 正文：忠实原文，保留所有数字、百分比、制程节点等关键指标；译文不超过700字
3. 禁止意译过度或省略数字与关键指标

请严格按照以下JSON格式返回，不要有任何额外文字：
{"title_zh": "<中文标题>", "text_zh": "<中文正文>"}
"""

TECH_SUMMARY_PROMPT = """
请为以下芯片行业文章生成面向「技术版周报」的精读摘要。原文可能是中文或英文，但输出必须全部为中文。

语言限制：所有输出字段必须使用中文，关键词也用中文；公司名、芯片型号、HBM、GAA、EUV、RISC-V 等专有名词可保留英文，禁止出现整句英文摘要。
如果原文正文包含登录、注册、导航、广告等页面噪声，不要翻译或复述这些噪声，应仅基于有效标题和正文生成中文摘要；无法判断时也要输出中文说明。

要求：
1. 标题：突出技术主题，不超过30字，用中文
2. 摘要：5-7句话，200~300字，用中文。按以下层次组织，每层至少一句：
   - 背景/问题：解决什么技术瓶颈或产业痛点
   - 技术路线或方案：核心技术手段是什么
   - 关键指标与数据：制程节点、性能、功耗、良率、带宽等，有数字必须保留
   - 对工程实践或产业的影响
3. 关键词：3-5个行业关键词，用中文，逗号分隔（技术缩写如 HBM、GAA 等可保留英文缩写）

请严格按照以下JSON格式返回，不要有任何额外文字：
{"title": "<中文标题>", "summary": "<中文摘要>", "keywords": "<中文关键词>"}
"""

BUSINESS_SUMMARY_PROMPT = """
请为以下芯片行业文章生成面向「商业版日报」的结构化摘要。原文可能是中文或英文，但输出必须全部为中文。

语言限制：所有输出字段必须使用中文，关键词也用中文；公司名、芯片型号、HBM、GAA、EUV、RISC-V 等专有名词可保留英文，禁止出现整句英文摘要。
如果原文正文包含登录、注册、导航、广告等页面噪声，不要翻译或复述这些噪声，应仅基于有效标题和正文生成中文摘要；无法判断时也要输出中文说明。

要求：
1. 标题：突出公司/产业动作，不超过30字，用中文
2. 摘要：4-6句话，200~300字，覆盖以下要素（有则必写）：
   - 主体：涉及哪家公司/机构/政策主体
   - 动作/事件：做了什么、发生了什么
   - 关键数字：金额、产能、市占率、增长率、时间节点等
   - 影响/意义：对行业/竞争格局/供应链的意义
   全部用中文，不要缩写或省略数字
3. 关键词：3-5个商业标签或实体，用中文，逗号分隔

请严格按照以下JSON格式返回，不要有任何额外文字：
{"title": "<中文标题>", "summary": "<中文摘要>", "keywords": "<中文关键词>"}
"""

# 每条新闻的「引导性追问」人设片段（§8）：由 analyzer.generate_summary 追加到摘要
# system prompt 末尾，随摘要一并产出，零额外 LLM 调用。可在 GUI「Prompt 高级编辑」改人设。
# 与报告级「延伸追问」（QUESTIONS_HTML，面向 Google 检索）不同：本功能每条一句、面向「思考」。
ARTICLE_FOLLOWUP_PROMPT = """
此外，请在上面返回的同一个 JSON 对象中额外增加一个字段 "followup"：
以「资深半导体行业分析师 + 资深芯片架构/封装工程师」的双重视角，针对本条讯息的具体内容，
提出 1 个能引发深度思考的开放式追问。要求：用中文、不超过 35 字；面向「为什么 / 意味着什么 /
对谁有何影响 / 下一步会怎样」这类思辨，而不是能被搜索引擎直接回答的事实查询；必须紧扣本条内容。
最终 JSON 形如：{"title": "...", "summary": "...", "keywords": "...", "followup": "<一句引导性追问>"}
"""

TECH_EXECUTIVE_SUMMARY_PROMPT = """
你是半导体技术分析师。以下是本周精选的 {n} 篇技术资讯标题和摘要，请生成一段简洁的「技术趋势概览」（不超过200字），提炼制造工艺、芯片架构、EDA工具、标准会议中的3-5个关键技术变化，用流畅的中文段落形式呈现（不要用bullet points）。
"""

TECH_WEEKLY_DIGEST_PROMPT = """
你是半导体技术分析师。以下是本周采集到的全部 {n} 条行业讯息（含未入选精读的条目），格式为「[分类] 标题 — 一句话内容」。

请撰写一份「本周讯息总结」，要求：
1. 覆盖全部讯息的整体面貌，不要只看前几条；
2. 按主题分 3-5 段（如制造与工艺、芯片架构与算力、EDA与设计方法、标准与生态、其他值得关注），每段 2-4 句；
3. 指出本周讯息的分布特点（哪个方向最热、有什么共同趋势、有什么孤立但重要的信号）；
4. 总长 300-450 字，用中文，分段输出，不要用 bullet points，不要逐条罗列。
"""

BUSINESS_EXECUTIVE_SUMMARY_PROMPT = """
你是半导体产业分析师。以下是本周精选的 {n} 篇商业动态标题和摘要，请生成一段简洁的「商业动态概览」（不超过200字），提炼公司竞争、供需变化、资本市场、政策管制、客户订单中的3-5个关键变化，用流畅的中文段落形式呈现（不要用bullet points）。
"""

# 兼容旧代码命名
RELEVANCE_PROMPT = TECH_RELEVANCE_PROMPT
SUMMARY_PROMPT = TECH_SUMMARY_PROMPT
EXECUTIVE_SUMMARY_PROMPT = TECH_EXECUTIVE_SUMMARY_PROMPT

# ============================================================
# HTTP 配置
# ============================================================

REQUEST_HEADERS = {
    "User-Agent": "Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36 (KHTML, like Gecko) Chrome/124.0.0.0 Safari/537.36",
    "Accept": "text/html,application/xhtml+xml,application/xml;q=0.9,*/*;q=0.8",
    "Accept-Language": "zh-CN,zh;q=0.9,en;q=0.8",
}
REQUEST_TIMEOUT = 15
REQUEST_DELAY = 1.2        # HTML 爬取间隔（秒）
RSS_FETCH_TIMEOUT = 25     # RSS 抓取超时（IR 类服务器较慢，留足余量）

# ============================================================
# 图片抓取（reporter 出报告时下载内嵌原文配图）
# ============================================================

# 单张图片 / 原文页抓取超时（秒）。海外图床较慢，留足余量。
IMAGE_FETCH_TIMEOUT = 12
# 图片下载失败后的重试次数，应对瞬时网络抖动。
IMAGE_FETCH_RETRIES = 1
# 并发下载图片的线程数，抵消加大超时带来的串行耗时。
IMAGE_FETCH_WORKERS = 10

# ============================================================
# 输出配置
# ============================================================

OUTPUT_DIR = "output"
OUTPUT_HTML = True
OUTPUT_JSON = True

# ============================================================
# GUI 配置覆盖层
# ------------------------------------------------------------
# 以上均为「代码默认值」。若存在 data/gui_settings.json（由轻量 GUI 保存），
# 在此把用户覆盖项合并到本模块全局上。手动运行 / 计划任务 / GUI 子进程
# 三者因此共用同一份设置。覆盖失败时静默回退默认值，不影响主流程。
# ============================================================

try:
    import settings_store as _settings_store
    _settings_store.apply_overrides(globals())
except Exception as _override_err:  # noqa: BLE001 — 覆盖层异常不得拖垮主流程
    import logging as _logging
    _logging.getLogger(__name__).warning(f"GUI 设置覆盖加载失败，使用默认值: {_override_err}")

# 覆盖可能改动了 TECH_* 源值，这里重新同步旧命名别名，确保别名指向最新值
MAX_ARTICLE_AGE_DAYS = TECH_ARTICLE_AGE_DAYS
TOP_N = TECH_TOP_N
CATEGORIES = TECH_CATEGORIES
RELEVANCE_PROMPT = TECH_RELEVANCE_PROMPT
SUMMARY_PROMPT = TECH_SUMMARY_PROMPT
EXECUTIVE_SUMMARY_PROMPT = TECH_EXECUTIVE_SUMMARY_PROMPT
