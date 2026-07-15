
"file": "https://cdn1.ozone.ru/s3/item-picture-6/f3/ce/f4ceae54b323213d3e61e59c323bd8e5.csv",
"report_type": "seller_products",
"params": { },
"created_at": "2021-11-25T14:54:55.688260Z"
}
}

复制
收回全部
报告清单
post
/v1/report/list

描述和范例控制台
回送之前已经生成的报告的列表。

header Parameters
Client-Id
required
string
用户识别号。

Api-Key
required
string
API-密钥。

Request Body schema: application/json
page
required
integer <int32>
页数。

page_size
required
integer <int32>
每页的值的数量：

默认值 — 100，
最大值 — 1,000。
report_type	
string
Default: "ALL"
报告类型：

ALL— 所有报告，
SELLER_PRODUCTS — 商品报告，
SELLER_STOCK — 商品库存报告，
SELLER_RETURNS — 退货报告，
SELLER_POSTINGS — 发货报告，
SELLER_DISCOUNTED — 减价商品报告，
MUTUAL_SETTLEMENT — 结算报告，
DOCUMENT_B2B_SALES — 面向法人客户的销售报告，
COMPENSATION_REPORT — 赔偿报告，
DECOMPENSATION_REPORT — 赔偿返还报告，
MARKED_PRODUCTS_SALES — 标签销售报告，
SELLER_PLACEMENT_BY_PRODUCTS — 按商品维度的存储服务费用报告，
SELLER_PLACEMENT_BY_SUPPLIES — 按交货维度的存储服务费用报告。
回复
200报告清单
Response Schema: application/json
result	
object
请求结果。

reports	
Array of objects
包含所有生成的报告的数组。

total	
integer <int32>
累计报告数。

400参数有误
403拒绝访问
404未找到答案
409请求冲突
500内部服务器错误
请求范例
Content type
application/json
{
"page": 1,
"page_size": 1000,
"report_type": "ALL"
}

复制
收回全部
回复范例
200400403404409500
Content type
application/json
{
"result": {
"reports": [
{
"code": "REPORT_seller_products_924336_1720170405_a9ea2f27-a473-4b13-99f9-d0cfcb5b1a69",
"status": "success",
"error": "",
"expires_at": "2025-11-12T19:55:28.249Z",
"file": "https://cdn1.ozone.ru/s3/item-picture-6/f3/ce/f4ceae54b323213d3e61e59c323bd8e5.csv",
"report_type": "seller_products",
"params": {
"visibility": "3"
},
"created_at": "2019-02-06T12:09:47.258062Z"
},
{
"code": "REPORT_seller_products_924336_1720170405_a9ea2f27-a473-4b13-99f9-d0cfcb5b1a69",
"status": "success",
"error": "",
"file": "https://cdn1.ozone.ru/s3/item-picture-6/f3/ce/f4ceae54b323213d3e61e59c323bd8e5.csv",
"report_type": "seller_products",
"params": {
"visibility": "3"
},
"created_at": "2019-02-15T08:34:32.267178Z"
}
],
"total": 2
}
}

复制
收回全部
商品报告
post
/v1/report/products/create

描述和范例控制台
获得带有商品数据的报告的方法。例如，Ozon的ID，商品的数量，价格，状态。 与个人中心中的商品和价格→商品列表→下载商品CSV部分相符。

一些空白的解释：

Ozon Product ID — 我们系统中的卖家系统中的商品标识符 — product_id。例如，如果你从Ozon仓库和你自己的仓库销售商品，Ozon商品识别码对他们来说将是相同的。
FBO Ozon SKU ID — 从Ozon仓库出售的卖家系统中的商品标识符 — product_id。
FBO Ozon SKU ID — 从您的仓库出售的卖家系统中的商品标识符 — product_id。
CrossBorder Ozon SKU — 从国外销售的卖家系统中的商品标识符 — product_id。
Barcode — 印在标签上的商品条形码。
Статус товара — 该商品是否可以在Ozon上购买。如果状态是 "准备出售"，则不能购买该商品。
Доступно на складе Ozon, шт — 可供销售的库存商品的数量。这个数额不包括保留商品。
Зарезервировано, шт — 一个状态为 "已预订 "的商品有多少单位。商品从Ozon收到订单的那一刻起就被保留了，直到它被包装好交付给客户。
Текущая цена с учётом скидки, руб. — 报告加载时商品的销售价格（包括折扣）。如果该商品参加了促销活动，则指定的价格没有折扣。
Базовая цена (цена до скидок), руб. — 无折扣的价格。
Цена Premium, руб. — 有Ozon Premium订阅买家的价格。
Рекомендованная цена, руб. — 商品在另一个市场上的最低价格。
Актуальная ссылка на рекомендованную цену — 在另一个市场上有推荐价格商品的链接。
header Parameters
Client-Id
required
string
用户识别号。

Api-Key
required
string
API-密钥。

Request Body schema: application/json
language	
string
Default: "DEFAULT"
回答所用语言：

RU — 俄语，
EN — 英语。
offer_id	
Array of strings
卖家系统中的商品标识符是商品货号。

search	
string
在记录内容中搜索，检查现货。

sku	
Array of integers <int64>
Ozon 系统中的商品标识符（SKU）。

visibility	
string
Default: "ALL"
Enum: "ALL" "VALIDATION_STATE_FAIL" "TO_SUPPLY" "IN_SALE" "REMOVED_FROM_SALE" "PARTIAL_APPROVED" "IMAGE_ABSENT" "ARCHIVED" "AUTO_ARCHIVED" "MANUAL_ARCHIVED"
按商品可见度过滤。

ALL——除了档案中的所有商品；
VALIDATION_STATE_FAIL——预审时未被验证器检查的商品；
TO_SUPPLY——准备出售的货物；
IN_SALE——正在销售的商品；
REMOVED_FROM_SALE——对买家隐藏的商品；
PARTIAL_APPROVED——商品存在警告，需要修改；
IMAGE_ABSENT——无图片的商品；
ARCHIVED——已归档商品；
AUTO_ARCHIVED——自动归档的商品；
MANUAL_ARCHIVED——手动归档的商品。
回复
200商品报告
Response Schema: application/json
result	
object
请求结果。

code	
string
报告的唯一识别码。要获取报告，请将此值传递到方法 /v1/report/info。

400参数有误
403拒绝访问
404未找到答案
409请求冲突
500内部服务器错误
请求范例
Content type
application/json
{
"language": "DEFAULT",
"offer_id": [ ],
"search": "",
"sku": [ ],
"visibility": "ALL"
}

复制
收回全部
回复范例
200400403404409500
Content type
application/json
{
"result": {
"code": "REPORT_seller_products_924336_1720170405_a9ea2f27-a473-4b13-99f9-d0cfcb5b1a69"
}
}

复制
收回全部
发货报告
post
/v1/report/postings/create

描述和范例控制台
带有订单信息的发货报告：

订单状态，
处理的开始日期，
订单号，
发货号码，
发货费用，
发货内容。 与个人中心中的FBO→来自Ozon仓库的订单和FBS→来自我的仓库的订单→CSV部分相符。
header Parameters
Client-Id
required
string
用户识别号。

Api-Key
required
string
API-密钥。

Request Body schema: application/json
filter
required
object
过滤器。

language	
string
Default: "DEFAULT"
回答所用语言：

RU — 俄语，
EN — 英语。
with	
object
额外的字段，需要添加到响应中。

回复
200发货报告
Response Schema: application/json
result	
object
请求结果。

code	
string
报告的唯一识别码。要获取报告，请将此值传递到方法 /v1/report/info。

400参数有误
403拒绝访问
404未找到答案
409请求冲突
500内部服务器错误
请求范例
Content type
application/json
{
"filter": {
"processed_at_from": "2021-09-02T17:10:54.861Z",
"processed_at_to": "2021-11-02T17:10:54.861Z",
"delivery_schema": [
"fbs"
],
"is_express": true,
"sku": [ ],
"cancel_reason_id": [ ],
"offer_id": "",
"status_alias": [ ],
"statuses": [ ],
"title": ""
},
"language": "DEFAULT",
"with": {
"additional_data": false,
"analytics_data": false,
"customer_data": false,
"jewelry_codes": false
}
}

复制
收回全部
回复范例
200400403404409500
Content type
application/json
{
"result": {
"code": "REPORT_seller_postings_514893_1722847571_32a3508c-6b53-408c-a212-6c97138d23ed"
}
}

复制
收回全部
财务报告
post
/v1/finance/cash-flow-statement/list

描述和范例控制台
从1号到15号以及从16号到31号的财务报告获取方式。 在请求一天的报告时，您将收到15天的报告。 对应卖家个人中心财务→余额→收入和支出模块。

header Parameters
Client-Id
required
string
用户识别号。

Api-Key
required
string
API-密钥。

Request Body schema: application/json
date
required
object
报告期限。

page
required
integer <int32>
请求返回中的页码。

with_details	
boolean
true，如果需要在响应中添加附加参数。

page_size
required
integer <int32>
页面上的元素数量。

回复
200财务报告
Response Schema: application/json
result	
object
方法操作结果。

cash_flows	
Array of objects
报告清单。

details	
Array of objects
细节信息。

page_count	
integer <int64>
含有报告的页数。

400参数有误
403拒绝访问
404未找到答案
409请求冲突
500内部服务器错误
请求范例
Content type
application/json
{
"date": {
"from": "2022-01-01T00:00:00.000Z",
"to": "2022-12-31T00:00:00.000Z"
},
"with_details": true,
"page": 1,
"page_size": 1
}

复制
收回全部
回复范例
200400403404409500
Content type
application/json
{
"result": {
"cash_flows": [
{
"commission_amount": 1437,
"currency_code": "string",
"item_delivery_and_return_amount": 1991,
"orders_amount": 1000,
"period": {
"begin": "2023-04-03T09:12:10.239Z",
"end": "2023-04-03T09:12:10.239Z",
"id": 11567022278500
},
"returns_amount": -3000,
"services_amount": 8471.28
}
],
"details": {
"period": {
"begin": "2023-04-03T09:12:10.239Z",
"end": "2023-04-03T09:12:10.239Z",
"id": 11567022278500
},
"payments": [
{
"payment": 0,
"currency_code": "string"
}
],
"begin_balance_amount": 0,
"delivery": {
"total": 0,
"amount": 0,
"delivery_services": {
"total": 0,
"items": [
{
"name": "string",
"price": 0
}
]
}
},
"return": {
"total": 0,
"amount": 0,
"return_services": {
"total": 0,
"items": [
{
"name": "string",
"price": 0
}
]
}
},
"loan": 0,
"invoice_transfer": 0,
"rfbs": {
"total": 0,
"transfer_delivery": 0,
"transfer_delivery_return": 0,
"compensation_delivery_return": 0,
"partial_compensation": 0,
"partial_compensation_return": 0
},
"services": {
"total": 0,
"items": [
{
"name": "string",
"price": 0
}
]
},
"others": {
"total": 0,
"items": [
{
"name": "string",
"price": 0
}
]
},
"end_balance_amount": 0
}
},
"page_count": 15
}

复制
收回全部
减价商品报告
post
/v1/report/discounted/create

描述和范例控制台
开始生成关于Ozon仓库中打折商品的报告。 Ozon可以自行处理一个商品，例如，如果它被损坏了。

请求结果将不是报告本身，而是其唯一的识别码。 要获取报告，请在 /v1/report/info 方法请求中发送ID。

从一个卖家账号每分钟可以发送1次请求。 与个人中心中的分析→报告→来自Ozon仓库的销售→由Ozon减价的商品部分相符。

header Parameters
Client-Id
required
string
用户识别号。

Api-Key
required
string
API-密钥。

Request Body schema: application/json
object
回复
200请求结果
Response Schema: application/json
code	
string
报告的唯一识别码。要获取报告，请将此值传递到方法 /v1/report/info。

400参数有误
403拒绝访问
404未找到答案
409请求冲突
500内部服务器错误
请求范例
Content type
application/json
{ }

复制
收回全部
回复范例
200400403404409500
Content type
application/json
{
"code": "REPORT_seller_products_924336_1720170405_a9ea2f27-a473-4b13-99f9-d0cfcb5b1a69"
}

复制
收回全部
关于FBS仓库库存报告
post
/v1/report/warehouse/stock

描述和范例控制台
报告包含仓库中可用和预留的商品数量的信息。 与个人中心中的FBO→物流管理→库存管理→以XLS格式下载部分相符。

查询的结果不是报告本身，而是其唯一ID。要获取报告，请在 /v1/report/info 方法的请求中发送ID。

header Parameters
Client-Id
required
string
用户识别号。

Api-Key
required
string
API-密钥。

Request Body schema: application/json
language	
string
Default: "DEFAULT"
回答所用语言：

RU — 俄语，
EN — 英语。
warehouseId
required
Array of strings <int64> <= 50 characters
仓库ID。 请求中参数值的限制。 最大值为 50。

回复
200查询结果
Response Schema: application/json
result	
object
请求结果。

code	
string
报告的唯一识别码。要获取报告，请将此值传递到方法 /v1/report/info。

400参数有误
403拒绝访问
404未找到答案
409请求冲突
500内部服务器错误
请求范例
Content type
application/json
{
"language": "DEFAULT",
"warehouseId": [
"1020002425123000"
]
}

复制
收回全部
回复范例
200400403404409500
Content type
application/json
{
"code": "REPORT_seller_products_924336_1720170405_a9ea2f27-a473-4b13-99f9-d0cfcb5b1a69"
}

复制
收回全部
生成带有标记商品的销售报告
post
/v1/report/marked-products-sales/create

描述和范例控制台
每个报告最多可包含 50,000 个商品标签代码。如需获取其余数据，请缩短报告生成周期。

header Parameters
Client-Id
required
string
用户识别号。

Api-Key
required
string
API-密钥。

Request Body schema: application/json
date	
object
报告生成周期。

回复
200查询结果
Response Schema: application/json
result	
object
请求结果。

code	
string
报告的唯一识别码。要获取报告，请将此值传递到方法 /v1/report/info。

default错误
请求范例
Content type
application/json
{
"date": {
"from": "string",
"to": "string"
}
}

复制
收回全部
回复范例
200default
Content type
application/json
{
"result": {
"code": "string"
}
}

复制
收回全部
财务报告
更多方法请见Premium方法。

商品销售报告 （第2版）
post
/v2/finance/realization

描述和范例控制台
该方法可获取2023年8月及之后期间的报告。更早期间的报告可在个人中心中查看。
当月与交付和退货有关的销售情况。订单取消与非赎回不包括其中。 与个人中心中的财务→文件→销售报告→商品销售报告部分相符。

报告将最迟于下个月的第五天发送。

header Parameters
Client-Id
required
string
用户识别号。

Api-Key
required
string
API-密钥。

Request Body schema: application/json
month
required
integer <int32>
月。

year
required
integer <int32>
年。

回复
200销售报告
Response Schema: application/json
result	
object
请求结果。

header	
object
报告标题页。

rows	
Array of objects
报告表格。

400参数错误
403访问禁止
404找不到答案
409询问冲突
500服务器内部错误
请求范例
Content type
application/json
{
"month": 0,
"year": 0
}

复制
收回全部
回复范例
200400403404409500
Content type
application/json
{
"result": {
"header": {
"contract_date": "string",
"contract_number": "string",
"currency_sys_name": "string",
"doc_date": "string",
"number": "string",
"payer_inn": "string",
"payer_kpp": "string",
"payer_name": "string",
"receiver_inn": "string",
"receiver_kpp": "string",
"receiver_name": "string",
"start_date": "string",
"stop_date": "string"
},
"rows": [
{
"commission_ratio": 0,
"delivery_commission": {
"amount": 0,
"bonus": 0,
"commission": 0,
"compensation": 0,
"price_per_instance": 0,
"quantity": 0,
"standard_fee": 0,
"bank_coinvestment": 0,
"stars": 0,
"total": 0
},
"item": {
"barcode": "string",
"name": "string",
"offer_id": "string",
"sku": 0
},
"return_commission": {
"amount": 0,
"bonus": 0,
"commission": 0,
"compensation": 0,
"price_per_instance": 0,
"quantity": 0,
"standard_fee": 0,
"bank_coinvestment": 0,
"stars": 0,
"total": 0
},
"rowNumber": 0,
"seller_price_per_instance": 0
}
]
}
}

复制
收回全部
按订单细分的商品销售报告
post
/v1/finance/realization/posting

描述和范例控制台
已送达和已退回商品销售的报告，带有每笔订单的详细信息。不包括取消和无人认领的订单。从现在起至2023年8月的报告可供您使用。

header Parameters
Client-Id
required
string
用户识别号。

Api-Key
required
string
API-密钥。

Request Body schema: application/json
month
required
integer <int32>
月。

year
required
integer <int32>
年。

回复
200按订单细分的销售报告
Response Schema: application/json
header	
object
报告标题页。

rows	
Array of objects
报告表格。

400参数错误
403访问被拒绝
404未找到响应
409请求冲突
500服务器内部错误
请求范例
Content type
application/json
{
"month": 2,
"year": 2025
}

复制
收回全部
回复范例
200400403404409500
Content type
application/json
{
"header": {
"contract_date": "string",
"contract_number": "string",
"currency_sys_name": "string",
"doc_date": "string",
"number": "string",
"payer_inn": "string",
"payer_kpp": "string",
"payer_name": "string",
"receiver_inn": "string",
"receiver_kpp": "string",
"receiver_name": "string",
"start_date": "string",
"stop_date": "string"
},
"rows": [
{
"commission_ratio": 0,
"delivery_commission": {
"amount": 0,
"bonus": 0,
"commission": 0,
"compensation": 0,
"price_per_instance": 0,
"quantity": 0,
"standard_fee": 0,
"bank_coinvestment": 0,
"stars": 0,
"total": 0
},
"item": {
"barcode": "string",
"name": "string",
"offer_id": "string",
"sku": 0
},
"return_commission": {
"amount": 0,
"bonus": 0,
"commission": 0,
"compensation": 0,
"price_per_instance": 0,
"quantity": 0,
"standard_fee": 0,
"bank_coinvestment": 0,
"stars": 0,
"total": 0
},
"row_number": 0,
"seller_price_per_instance": 0,
"order": {
"posting_number": "string",
"created_date": "string"
},
"legal_entity_document": {
"number": "string",
"sale_date": "string"
}
}
]
}

复制
收回全部
交易清单
post
/v3/finance/transaction/list

描述和范例控制台
该方法即将废弃，并将于2026年7月6日停用。请切换到/v1/finance/accrual/postings, /v1/finance/accrual/types, /v1/finance/accrual/by-day。

请使用顺序发送请求的方式。
数据可能与个人中心中的信息不一致。
返回所有应计项目的详细信息。 在一次请求中可获取信息的最长时间为1月。

如果请求中未指出 posting_number, 那么响应将包含指定时间段内的所有订单或特定订单类型。

header Parameters
Client-Id
required
string
用户识别号。

Api-Key
required
string
API-密钥。

Request Body schema: application/json
filter	
posting_number (object) or date (object)
过滤器。

page
required
integer <int64>
请求中返回的页码。

page_size
required
integer <int64> <= 1000
每页的元素数。

回复
200交易清单
Response Schema: application/json
result	
object
询问结果。

operations	
Array of objects
操作信息。

page_count	
integer <int64>
页数。如果为0，则说明已无页面。

row_count	
integer <int64>
所有页面上的交易数量。如果为0，说明已无交易。

400参数错误
403拒绝访问
404无响应
409请求冲突
500服务器内部错误
请求范例
Content type
application/json
{
"filter": {
"date": {
"from": "2021-11-01T00:00:00.000Z",
"to": "2021-11-02T00:00:00.000Z"
},
"operation_type": [ ],
"posting_number": "",
"transaction_type": "all"
},
"page": 1,
"page_size": 1000
}

复制
收回全部
回复范例
200400403404409500
Content type
application/json
{
"result": {
"operations": [
{
"operation_id": 11401182187840,
"operation_type": "MarketplaceMarketingActionCostOperation",
"operation_date": "2021-11-01 00:00:00",
"operation_type_name": "商品推销服务",
"delivery_charge": 0,
"return_delivery_charge": 0,
"accruals_for_sale": 0,
"sale_commission": 0,
"amount": -6.46,
"type": "services",
"posting": {
"delivery_schema": "",
"order_date": "",
"posting_number": "",
"warehouse_id": 0
},
"items": [ ],
"services": [ ]
}
],
"page_count": 1,
"row_count": 355
}
}

复制
收回全部
清单数目
post
/v3/finance/transaction/totals

描述和范例控制台
该方法即将废弃，并将于2026年7月6日停用。请切换到/v1/finance/accrual/postings, /v1/finance/accrual/types, /v1/finance/accrual/by-day。

数据可能与个人中心中的信息不一致。
返回指定时间的清单总数。

header Parameters
Client-Id
required
string
用户识别号。

Api-Key
required
string
API-密钥。

Request Body schema: application/json
One of posting_numberdate
date	
object
按日期过滤。

posting_number
required
string
发货号。

transaction_type	
string
操作类型：

all — 所有,
orders — 订单,
returns — 退货和取消,
services — 服务费,
compensation — 补贴,
transferDelivery — 快递费用,
other — 其他。
回复
200清单数目
Response Schema: application/json
result	
object
询问结果。

accruals_for_sale	
number <double>
指定期间内商品的总成本和退货。

compensation_amount	
number <double>
补贴。

money_transfer	
number <double>
根据“卖方选择交货”计划工作时的交货和退货费用。

others_amount	
number <double>
其他应计费用。

processing_and_delivery	
number <double>
运输处理、订单装配、干线、最后一英里以及自2021年2月1日起引入新的佣金和费率前的快递服务费。

干线 —— 集群之间的货物交付。

最后一英里 —— 从订单交付点、自提点和快递员到买家处的快递。

refunds_and_cancellations	
number <double>
干线返回、退货处理、取消和非赎回、2021年2月1日起引入新佣金和税率之前退货价格。

干线 —— 集群之间的货物交付。

最后一英里 —— 从订单交付点、自提点和快递员到买家处的快递。

sale_commission	
number <double>
商品预售时预扣的佣金数额，退货时返还的佣金数。

services_amount	
number <double>
与商品交付和退货没有直接关系的附加服务成本。例如，促销或商品放置。

400参数错误
403拒绝访问
404无响应
409请求冲突
500服务器内部错误
请求范例
Content type
application/json
{
"date": {
"from": "2021-11-01T00:00:00.000Z",
"to": "2021-11-02T00:00:00.000Z"
},
"posting_number": "",
"transaction_type": "all"
}

复制
收回全部
回复范例
200400403404409500
Content type
application/json
{
"result": {
"accruals_for_sale": 96647.58,
"sale_commission": -11456.65,
"processing_and_delivery": -24405.68,
"refunds_and_cancellations": -330,
"services_amount": -1307.57,
"compensation_amount": 0,
"money_transfer": 0,
"others_amount": 113.05
}
}

复制
收回全部
赔偿报告
post
/v1/finance/compensation

描述和范例控制台
用于获取赔偿报告的方法。与卖家个人中心中 财务 → 文件 → 赔偿及其他应计费用 部分的报告一致。

Request Body schema: application/json
date
required
string
报告周期格式为 YYYY-MM。

language	
string
Default: "RU"
报告语言：

RU — 俄语，
EN — 英语。
回复
200赔偿报告
Response Schema: application/json
result	
object
请求结果。

code	
string
报告的唯一标识符。要获取报告，请将该值传递到方法 /v1/report/info。

default错误
请求范例
Content type
application/json
{
"date": "2023-09",
"language": "RU"
}

复制
收回全部
回复范例
200default
Content type
application/json
{
"result": {
"code": "string"
}
}

复制
收回全部
赔偿返还报告
post
/v1/finance/decompensation

描述和范例控制台
用于获取赔偿返还报告的方法。与卖家个人中心中 财务 → 文件 → 赔偿及其他应计费用 部分的报告一致。

Request Body schema: application/json
date
required
string
报告周期格式为 YYYY-MM。

language	
string
Default: "RU"
报告语言：

RU — 俄语，
EN — 英语。
回复
200赔偿返还报告
Response Schema: application/json
result	
object
请求结果。

code	
string
报告的唯一标识符。要获取报告，请将该值传递到方法 /v1/report/info。

default错误
请求范例
Content type
application/json
{
"date": "2023-09",
"language": "RU"
}

复制
收回全部
回复范例
200default
Content type
application/json
{
"result": {
"code": "string"
}
}

复制
收回全部
卖家评级
在 Ozon 平台运营时，卖家需要遵守关于服务质量、配送时效以及与客户沟通等方面的要求。评级系统用于反映卖家的服务质量，其中部分指标对买家可见——例如商品评级和价格指数。

获取错误指数：FBS 和 rFBS
post
/v1/rating/index/fbs/info

描述和范例控制台
header Parameters
Client-Id
required
string
用户识别号。

Api-Key
required
string
API-密钥。

回复
200错误指数
Response Schema: application/json
currency_code	
string
错误处理费用的币种代码。

defects	
Array of objects
按天计算的错误指数。

index	
number <double>
周期内的错误指数数值。

period_from	
string
计算周期开始日期（格式YYYY-MM-DD）。

period_to	
string
计算周期结束日期（格式YYYY-MM-DD）。

processing_costs_sum	
number <double>
周期内的错误处理费用。

default错误
回复范例
200default
Content type
application/json
{
"currency_code": "string",
"defects": [
{
"date": "string",
"index_by_date": 0,
"processing_costs_sum_by_date": 0
}
],
"index": 0,
"period_from": "string",
"period_to": "string",
"processing_costs_sum": 0
}

复制
收回全部
影响错误指数的货件列表：FBS 和 rFBS
post
/v1/rating/index/fbs/posting/list

描述和范例控制台
header Parameters
Client-Id
required
string
用户识别号。

Api-Key
required
string
API-密钥。

Request Body schema: application/json
cursor	
string
用于获取下一批数据的指针。

filter
required
object
筛选器。

limit
required
integer <int64> <= 1000
返回结果中的数值数量。

回复
200货件列表
Response Schema: application/json
cursor	
string
用于获取下一批数据的指针。

errors	
Array of objects
影响错误指数的货件。

has_next	
boolean
true，表示查询结果未包含所有货件。

default错误
请求范例
Content type
application/json
{
"cursor": "string",
"filter": {
"date_from": "2019-08-24T14:15:22Z",
"date_to": "2019-08-24T14:15:22Z",
"posting_numbers": [
"string"
]
},
"limit": 0
}

复制
收回全部
回复范例
200default
Content type
application/json
{
"cursor": "string",
"errors": [
{
"charge_percent": 0,
"charge_price": 0,
"charge_price_currency_code": "string",
"delivery_schema": "string",
"error_at": "2019-08-24T14:15:22Z",
"has_grace_status": true,
"index": 0,
"posting_error_type": "UNSPECIFIED",
"posting_number": "string",
"product_price": 0,
"product_price_currency_code": "string"
}
],
"has_next": true
}

复制
收回全部
测试方法
其他方法
将订单拆分为不带备货的货件
post
/v1/posting/fbs/split

描述和范例控制台
您可以在 讨论 的评论中对此方法提供反馈 在 Ozon for dev 开发者社区中。

Request Body schema: application/json
posting_number
required
string
货件编号。

postings
required
Array of objects
要拆分订单的货件项列表。每个请求只能拆分一个订单。

回复
200订单已拆分
Response Schema: application/json
parent_posting	
object
原始货件的信息。

postings	
Array of objects
订单被拆分后的货件列表。

default错误
请求范例
Content type
application/json
{
"posting_number": "string",
"postings": [
{
"products": [
{
"product_id": 0,
"quantity": 0
}
]
}
]
}

复制
收回全部
回复范例
200default
Content type
application/json
{
"parent_posting": {
"posting_number": "string",
"products": [
{
"product_id": 0,
"quantity": 0
}
]
},
"postings": [
{
"posting_number": "string",
"products": [
{
"product_id": 0,
"quantity": 0
}
]
}
]
}

复制
收回全部
获取FBS和rFBS仓库库存信息
post
/v1/product/info/warehouse/stocks

描述和范例控制台
您可以在 讨论 的评论中对此方法提供反馈 在 Ozon for dev 开发者社区中。

Request Body schema: application/json
cursor	
string
用于选择下一批数据的指针。

limit
required
integer <int64> [ 1 .. 1000 ]
每页显示的数量。

warehouse_id
required
integer <int64>
仓库标识符。

回复
200FBS和rFBS仓库中的商品数量
Response Schema: application/json
cursor	
string
用于选择下一批数据的指针。 如果该参数为空，则没有更多数据了。

has_next	
boolean
标记是否返回了所有商品：

true——请使用不同的cursor值重新请求，以获取剩余的值；
false——响应中已包含所有值。
stocks	
Array of objects
商品库存信息。

default错误
请求范例
Content type
application/json
{
"cursor": "",
"limit": 10,
"warehouse_id": 1020003080073000
}

复制
收回全部
回复范例
200default
Content type
application/json
{
"stocks": [
{
"sku": 147035011,
"product_id": 28743,
"offer_id": "02105020-35",
"warehouse_id": 1020003080073000,
"present": 1000,
"reserved": 0,
"free_stock": 1000,
"updated_at": "2025-09-15T10:36:24.417498Z"
}
],
"has_next": false,
"cursor": "147035011"
}

复制
收回全部
管理按数量折扣
post
/v1/product/stairway-discount/by-quantity/set

描述和范例控制台
根据订单中商品数量设置或删除商品折扣。

您可以在 讨论 的评论中对此方法提供反馈 在 Ozon for dev 开发者社区中。

header Parameters
Client-Id
required
string
用户识别号。

Api-Key
required
string
API-密钥。

Request Body schema: application/json
stairways
required
Array of objects
多个商品的按数量折扣信息。

suppress_warnings	
boolean
传递 true 可忽略警告并设置折扣。

回复
200折扣设置已更改
Response Schema: application/json
accepted	
boolean
true，表示请求已接收。请使用方法/v1/product/stairway-discount/by-quantity/get来查看折扣修改结果。

errors	
Array of objects
错误描述。

warnings	
Array of objects
警告描述。

400参数错误
403访问被拒绝
404响应未找到
409请求冲突
500服务器内部错误
请求范例
Content type
application/json
{
"stairways": [
{
"enabled": true,
"sku": 0,
"stairway": {
"steps": [
{
"discount": 0,
"quantity": 0,
"step": 0
}
]
}
}
],
"suppress_warnings": true
}

复制
收回全部
回复范例
200400403404409500
Content type
application/json
{
"accepted": true,
"errors": [
{
"data": [
{
"code": "string",
"field": "string",
"message": "string",
"step": 0,
"value": "string"
}
],
"sku": 0
}
],
"warnings": [
{
"data": [
{
"code": "string",
"field": "string",
"message": "string",
"step": 0,
"value": "string"
}
],
"sku": 0
}
]
}

复制
收回全部
获取按数量折扣信息
post
/v1/product/stairway-discount/by-quantity/get

描述和范例控制台
返回根据订单中商品数量计算的商品折扣信息。

您可以在 讨论 的评论中对此方法提供反馈 在 Ozon for dev 开发者社区中。

header Parameters
Client-Id
required
string
用户识别号。

Api-Key
required
string
API-密钥。

Request Body schema: application/json
skus
required
Array of strings <int64> <= 5000 items
需要返回内容评级的商品SKU列表。

回复
200信息已获取
Response Schema: application/json
stairways	
Array of objects
单个商品的按数量折扣信息。

Array ()
enabled	
boolean
true，表示数量折扣已启用。

sku	
integer <int64>
Ozon系统中的商品标识符——SKU。

stairway	
object
按数量折扣等级信息。

status	
string
Enum: "IN_PROCESS" "ERROR" "SUCCESS"
按数量折扣变更状态。可能的取值：

ERROR——修改折扣时出错。请再次调用方法 /v1/product/stairway-discount/by-quantity/set。
IN_PROCESS——修正正在处理中。
SUCCESS——折扣修改已成功应用到商品。
400参数错误
403访问被拒绝
404响应未找到
409请求冲突
500服务器内部错误
请求范例
Content type
application/json
{
"skus": [
"string"
]
}

复制
收回全部
回复范例
200400403404409500
Content type
application/json
{
"stairways": [
{
"enabled": true,
"sku": 0,
"stairway": {
"steps": [
{
"discount": 0,
"quantity": 0,
"step": 0
}
]
},
"status": "IN_PROCESS"
}
]
}

复制
收回全部
获取余额报告
post
/v1/finance/balance

描述和范例控制台
对应卖家个人中心 财务 → 余额 模块。

您可以在 讨论 的评论中对此方法提供反馈 在 Ozon for dev 开发者社区中。

Request Body schema: application/json
date_from
required
string <date-time>
报告期开始日期，格式为 YYYY-MM-DD。

date_to
required
string <date-time>
报告期结束日期，格式为 YYYY-MM-DD。date_from 与 date_to 之间的最⻓间隔为30 天。

回复
200余额报告
Response Schema: application/json
cashflows	
object
收入和支出信息。

total	
object
周期内的余额总体数据。

default错误
请求范例
Content type
application/json
{
"date_from": "2019-08-24",
"date_to": "2019-09-24"
}

复制
收回全部
回复范例
200default
Content type
application/json
{
"cashflows": {
"returns": {
"amount": {
"currency_code": "string",
"value": 0
},
"amount_details": {
"partner_programs": {
"currency_code": "string",
"value": 0
},
"points_for_discounts": "string",
"revenue": {
"currency_code": "string",
"value": 0
}
},
"fee": {
"currency_code": "string",
"value": 0
}
},
"sales": {
"amount": {
"currency_code": "string",
"value": 0
},
"amount_details": {
"partner_programs": {
"currency_code": "string",
"value": 0
},
"points_for_discounts": "string",
"revenue": {
"currency_code": "string",
"value": 0
}
},
"fee": {
"currency_code": "string",
"value": 0
}
},
"services": [
{
"amount": {
"currency_code": "string",
"value": 0
},
"name": "string"
}
]
},
"total": {
"accrued": {
"currency_code": "string",
"value": 0
},
"closing_balance": {
"currency_code": "string",
"value": 0
},
"opening_balance": {
"currency_code": "string",
"value": 0
},
"payments": [
{
"currency_code": "string",
"value": 0
}
]
}
}

复制
收回全部
获取用于确定商品类目的提示
post
/v1/description-category/tips

描述和范例控制台
您可以在 讨论 的评论中对此方法提供反馈 在 Ozon for dev 开发者社区中。

header Parameters
Client-Id
required
string
用户识别号。

Api-Key
required
string
API-密钥。

Request Body schema: application/json
type_id	
Array of strings <int64>
商品类型标识符。可通过方法 /v1/description-category/tree获取。

回复
200提示
Response Schema: application/json
result	
Array of objects
提示列表。

Array ()
images_url	
Array of strings
相似商品图片链接。

info_url	
string
指向Ozon商品橱窗的链接，其中包含相似商品及其信息。

type_id	
integer <int64>
商品类型标识符。

default错误
请求范例
Content type
application/json
{
"type_id": [
"string"
]
}

复制
收回全部
回复范例
200default
Content type
application/json
{
"result": [
{
"images_url": [
"string"
],
"info_url": "string",
"type_id": 0
}
]
}

复制
收回全部
获取折扣申请列表
post
/v2/actions/discounts-task/list

描述和范例控制台
返回买家希望以折扣价格购买的商品列表。

您可以在 讨论 的评论中对此方法提供反馈 在 Ozon for dev 开发者社区中。

header Parameters
Client-Id
required
string
用户识别号。

Api-Key
required
string
API-密钥。

Request Body schema: application/json
last_id	
integer <int64>
页面上最后一个值的标识符。首次请求请留空。

limit	
integer <int64> <= 50
Default: 50
Enum: 5 10 15 20 30 50
每页最大申请数量。

status	
string
Default: "ALL"
Enum: "ALL" "NEW" "APPROVED" "DECLINED"
折扣申请状态：

ALL——全部状态，
NEW——新建，
APPROVED——已批准，
DECLINED——已拒绝。
回复
200申请列表
Response Schema: application/json
tasks	
Array of objects
申请列表。

Array ()
approved_discount	
number <double>
卖家批准的折扣金额（卢布）。如果卖家未批准申请，请传入 0。

approved_price	
number <double>
批准价格。

approved_quantity_max	
integer <uint64>
批准的最大商品数量。

auto_moderated_info	
object
申请自动审核信息。

created_at	
string <date-time>
申请创建日期。

edited_till	
string <date-time> YYYY-MM-DD
可修改决定的时间。

edited_till_duration	
integer <uint64>
可修改决定的时间（秒）。

email	
string
处理申请的卖家员工邮箱地址。

end_at	
string <date-time>
申请有效期结束时间。

end_at_duration	
integer <uint64>
申请有效期结束时间（秒）。

first_name	
string
处理申请的卖家员工名字。

id	
integer <uint64>
申请标识符。

is_auto_moderated	
boolean
true，表示审核为自动审核。

last_name	
string
处理申请的卖家员工姓氏。

min_auto_price	
number <double>
自动应用折扣与促销后的最低价格值。

moderated_at	
string <date-time>
审核日期：查看、批准或拒绝申请的日期。

name	
string
商品名称。

original_price	
number <double>
商品在所有折扣前的价格。

patronymic	
string
处理申请的卖家员工父名（中间名）。

reduction_factor	
number <double>
创建申请时买家价格与卖家价格之间的差值。

requested_discount	
number <double>
折扣百分比。

requested_price	
number <double>
申请价格。

requested_quantity_max	
integer <uint64>
请求的最大商品数量。

sku	
integer <uint64>
Ozon 系统中的商品标识符——SKU。

status	
string
Default: "ALL"
Enum: "ALL" "NEW" "APPROVED" "DECLINED"
折扣申请状态：

ALL——全部状态，
NEW——新建，
APPROVED——已批准，
DECLINED——已拒绝。
default错误
请求范例
Content type
application/json
{
"last_id": 0,
"limit": 50,
"status": "ALL"
}

复制
收回全部
回复范例
200default
Content type
application/json
{
"tasks": [
{
"approved_discount": 0,
"approved_price": 0,
"approved_quantity_max": 0,
"auto_moderated_info": {
"max_percent": 0,
"max_price": 0,
"min_percent": 0,
"min_price": 0
},
"created_at": "2019-08-24T14:15:22Z",
"edited_till": "2019-08-24T14:15:22Z",
"edited_till_duration": 0,
"email": "string",
"end_at": "2019-08-24T14:15:22Z",
"end_at_duration": 0,
"first_name": "string",
"id": 0,
"is_auto_moderated": true,
"last_name": "string",
"min_auto_price": 0,
"moderated_at": "2019-08-24T14:15:22Z",
"name": "string",
"original_price": 0,
"patronymic": "string",
"reduction_factor": 0,
"requested_discount": 0,
"requested_price": 0,
"requested_quantity_max": 0,
"sku": 0,
"status": "ALL"
}
]
}

复制
收回全部
新增了用于设置商品在Ozon和Ozon Select橱窗可见性的Beta方法。
post
/v1/product/visibility/set

描述和范例控制台
该方法适用于已开通Ozon Select的卖家。
您可以在 讨论 的评论中对此方法提供反馈 在 Ozon for dev 开发者社区中。

Request Body schema: application/json
item_placement
required
Array of objects
商品可见性信息。

回复
200商品可见性已设置
Response Schema: application/json
items	
Array of objects
商品可见性信息。

items_errors	
Array of objects
存在错误的商品。

default错误
请求范例
Content type
application/json
{
"item_placement": [
{
"placement": "OZON",
"sku": 0
}
]
}

复制
收回全部
回复范例
200default
Content type
application/json
{
"items": [
{
"select_permission": "UNSPECIFIED",
"seller_item_placement": "UNSPECIFIED",
"seller_item_placement_list": [
"UNSPECIFIED"
],
"showcases_visibility": "UNSPECIFIED",
"showcases_visibility_list": [
"UNSPECIFIED"
],
"sku": 0,
"warnings": [
"string"
]
}
],
"items_errors": [
{
"code": "string",
"sku": 0
}
]
}

复制
收回全部
获取按货件统计的应计项目
post
/v1/finance/accrual/postings

描述和范例控制台
您可以在 讨论的评论中对此方法提供反馈 在 Ozon for dev 开发者社区中。

header Parameters
Client-Id
required
string
用户识别号。

Api-Key
required
string
API-密钥。

Request Body schema: application/json
posting_numbers
required
Array of strings [ 1 .. 200 ] items
货件编号。

回复
200按货件统计的应计项目
Response Schema: application/json
posting_accruals	
Array of objects
按货件统计的应计项目列表。

Array ()
accruals	
Array of objects
应计项目列表。

posting_number	
string
货件编号。

default错误
请求范例
Content type
application/json
{
"posting_numbers": [
"string"
]
}

复制
收回全部
回复范例
200default
Content type
application/json
{
"posting_accruals": [
{
"accruals": [
{
"accrual_date": "string",
"accrued": {
"amount": "string",
"currency": "string"
},
"quantity": 0,
"seller_price": {
"amount": "string",
"currency": "string"
},
"sku": 0,
"type_id": 0
}
],
"posting_number": "string"
}
]
}

复制
收回全部
获取应计项目参考信息
post
/v1/finance/accrual/types

描述和范例控制台
您可以在 讨论的评论中对此方法提供反馈 在 Ozon for dev 开发者社区中。

header Parameters
Client-Id
required
string
用户识别号。

Api-Key
required
string
API-密钥。

回复
200应计项目参考信息
Response Schema: application/json
accrual_types	
Array of objects
应计项目相关信息。

Array ()
description	
string
应计项目说明。

id	
integer <int32>
应计项目标识符。

name	
string
应计项目名称。

default错误
回复范例
200default
Content type
application/json
{
"accrual_types": [
{
"description": "string",
"id": 0,
"name": "string"
}
]
}

复制
收回全部
获取某日应计项目
post
/v1/finance/accrual/by-day

描述和范例控制台
您可以在 讨论的评论中对此方法提供反馈 在 Ozon for dev 开发者社区中。

header Parameters
Client-Id
required
string
用户识别号。

Api-Key
required
string
API-密钥。

Request Body schema: application/json
date
required
string YYYY-MM-DD
应计日期。最早可查询日期为2022年1月1日。

last_id
required
string
页面上最后一个值的标识符。首次请求请留空。

要获取后续值，请指定上一次请求响应中的 last_id。

回复
200某日应计项目
Response Schema: application/json
accruals	
Array of objects
应计项目列表。

last_id	
string
页面中最后一个值的标识符。

default错误
请求范例
Content type
application/json
{
"date": "string",
"last_id": "string"
}

复制
收回全部
回复范例
200default
Content type
application/json
{
"accruals": [
{
"accrued_category": "UNSPECIFIED",
"date": "string",
"item_fees": {
"fees": [
{
"fees": [
{
"accrued": {
"amount": "string",
"currency": "string"
},
"type_id": 0
}
],
"sku": 0
}
]
},
"non_item_fee": {
"accrued": {
"amount": "string",
"currency": "string"
},
"type_id": 0
},
"posting": {
"delivery_schema": "string",
"delivery_speed": 0,
"products": [
{
"commission": {
"bonus": {
"amount": "string",
"currency": "string"
},
"coinvestment": {
"amount": "string",
"currency": "string"
},
"commission": {
"amount": "string",
"currency": "string"
},
"commission_ratio": "string",
"sale_amount": {
"amount": "string",
"currency": "string"
},
"sale_commission": {
"amount": "string",
"currency": "string"
},
"sale_price": {
"amount": "string",
"currency": "string"
},
"seller_price": {
"amount": "string",
"currency": "string"
}
},
"delivery": {
"services": [
{
"accrued": {
"amount": null,
"currency": null
},
"type_id": 0
}
],
"total_accrued": {
"amount": "string",
"currency": "string"
}
},
"sku": 0
}
]
},
"total_amount": {
"amount": "string",
"currency": "string"
},
"accrual_id": 0,
"unit_number": "string"
}
],
"last_id": "string"
}

复制
收回全部
获取商品可见性信息
post
/v1/product/visibility/info

描述和范例控制台
您可以在 讨论 的评论中对此方法提供反馈 在 Ozon for dev 开发者社区中。

header Parameters
Client-Id
required
string
用户识别号。

Api-Key
required
string
API-密钥。

Request Body schema: application/json
skus	
Array of strings <int64> [ 1 .. 350 ] items
Ozon系统中的商品标识符—— SKU。

回复
200商品可见性信息
Response Schema: application/json
items	
Array of objects
商品列表。

Array ()
showcases_visibility	
string
Default: "UNSPECIFIED"
Enum: "UNSPECIFIED" "OZON" "SELECT" "OZON_SELECT" "NONE"
商品展示在哪些橱窗中：

UNSPECIFIED——未指定；
OZON——仅在Ozon展示；
SELECT——仅在Select展示；
OZON_SELECT——在Select和Ozon展示；
NONE——商品在所有橱窗均隐藏。
sku	
integer <int64>
商品在Ozon系统中的标识符——SKU。

default错误
请求范例
Content type
application/json
{
"skus": [
"string"
]
}

复制
收回全部
回复范例
200default
Content type
application/json
{
"items": [
{
"showcases_visibility": "UNSPECIFIED",
"sku": 0
}
]
}

复制
收回全部
标签代码管理
检查并保存份数数据
post
/v6/fbs/posting/product/exemplar/set

描述和范例控制台
异步方法：

检查在“诚信标志”系统中流通份数的存在性；
保存份数数据。
为了获取已创建样件的数据，请使用 /v6/fbs/posting/product/exemplar/create-or-get 方式。

如果您在一批货件中有多个相同的商品, 请为货件中的每个商品指出一个 product_id 和一组 exemplars。

请始终传输全套份数和商品数据。

例如，如果在您的系统里有10份。您已赋值并检查和储存。然后在自己的系统中还添加了60份。 当重新提交份数以供审查和保存时，请指出所有新旧份数。

响应代码200并不保证商品数据已被接受。 它表示已创建任务以添加信息。 要检查任务状态，请使用方法 /v5/fbs/posting/product/exemplar/status。

您可以在 讨论 的评论中对此方法提供反馈 在 Ozon for dev 开发者社区中。

header Parameters
Client-Id
required
string
用户识别号。

Api-Key
required
string
API-密钥。

Request Body schema: application/json
multi_box_qty	
integer <int32>
商品包装的箱子数量。

posting_number
required
string
发货号。

products
required
Array of objects
商品清单。

回复
200请求已处理
default错误
请求范例
Content type
application/json
{
"multi_box_qty": 0,
"posting_number": "string",
"products": [
{
"exemplars": [
{
"exemplar_id": 0,
"gtd": "string",
"is_gtd_absent": true,
"is_rnpt_absent": true,
"marks": [
{
"mark": "string",
"mark_type": "string"
}
],
"rnpt": "string"
}
],
"product_id": 0
}
]
}

复制
收回全部
回复范例
default
Content type
application/json
{
"code": 0,
"details": [
{
"typeUrl": "string",
"value": "string"
}
],
"message": "string"
}

复制
收回全部
获取已创建样件数据
post
/v6/fbs/posting/product/exemplar/create-or-get

描述和范例控制台
此方法用于获取货件中商品的信息，这些信息通过方法 /v6/fbs/posting/product/exemplar/set 传递。

请使用此方法获取 exemplar_id。

您可以在 讨论 的评论中对此方法提供反馈 在 Ozon for dev 开发者社区中。

header Parameters
Client-Id
required
string
用户识别号。

Api-Key
required
string
API-密钥。

Request Body schema: application/json
posting_number
required
string
发货号。

回复
200样件数据
Response Schema: application/json
multi_box_qty	
integer <int32>
商品包装的箱子数量。

posting_number	
string
发货号。

products	
Array of objects
商品清单。

default错误
请求范例
Content type
application/json
{
"posting_number": "string"
}

复制
收回全部
回复范例
200default
Content type
application/json
{
"multi_box_qty": 0,
"posting_number": "string",
"products": [
{
"exemplars": [
{
"exemplar_id": 0,
"gtd": "string",
"is_gtd_absent": true,
"is_rnpt_absent": true,
"marks": [
{
"mark": "string",
"mark_type": "string"
}
],
"rnpt": "string"
}
],
"has_imei": true,
"is_gtd_needed": true,
"is_jw_uin_needed": true,
"is_mandatory_mark_needed": true,
"is_mandatory_mark_possible": true,
"is_rnpt_needed": true,
"product_id": 0,
"quantity": 0
}
]
}

复制
收回全部
获取样件添加状态
post
/v5/fbs/posting/product/exemplar/status

描述和范例控制台
获取在 /v6/fbs/posting/product/exemplar/set 方式中传输的样件添加状态的方式。 同时还归还这些样件的数据。

您可以在 讨论 的评论中对此方法提供反馈 在 Ozon for dev 开发者社区中。

header Parameters
Client-Id
required
string
用户识别号。

Api-Key
required
string
API-密钥。

Request Body schema: application/json
posting_number
required
string
发货号。

回复
200样件验证状态
Response Schema: application/json
posting_number	
string
发货号。

products	
Array of objects
商品清单。

status	
string
所有样件和备货可用性的验证状态：

ship_available——可以备货，
ship_not_available——无法备货，
validation_in_process——样件正在验证中，
update_available——可以编辑商品实例信息，
update_not_available——无法编辑商品实例信息。
default错误
请求范例
Content type
application/json
{
"posting_number": "string"
}

复制
收回全部
回复范例
200default
Content type
application/json
{
"posting_number": "string",
"products": [
{
"exemplars": [
{
"exemplar_id": 0,
"gtd": "string",
"gtd_check_status": "string",
"gtd_error_codes": [
"string"
],
"is_gtd_absent": true,
"is_rnpt_absent": true,
"marks": [
{
"check_status": "string",
"error_codes": [
"string"
],
"mark": "string",
"mark_type": "string"
}
],
"rnpt": "string",
"rnpt_check_status": "string",
"rnpt_error_codes": [
"string"
]
}
],
"product_id": 0
}
],
"status": "string"
}

复制
收回全部
标志代码验证
post
/v5/fbs/posting/product/exemplar/validate

描述和范例控制台
用于校验代码是否符合“Chestny ZNAK”系统对字符数量和组成的要求，以及其他标识系统的要求的方式。

如果您没有货物报关单号，那么您可以不输入。

您可以在 讨论 的评论中对此方法提供反馈 在 Ozon for dev 开发者社区中。

header Parameters
Client-Id
required
string
用户识别号。

Api-Key
required
string
API-密钥。

Request Body schema: application/json
posting_number
required
string
发货号。

products
required
Array of objects
商品清单。

回复
200验证结果
Response Schema: application/json
products	
Array of objects
商品清单。

Array ()
error	
string
错误代码。

exemplars	
Array of objects
副本信息。

product_id	
integer <int64>
Ozon系统中的商品ID — SKU。

valid	
boolean
验证结果。如果所有样件的代码都符合要求，那么结果将为 true。

default错误
请求范例
Content type
application/json
{
"posting_number": "string",
"products": [
{
"exemplars": [
{
"gtd": "string",
"marks": [
{
"mark": "string",
"mark_type": "string"
}
],
"rnpt": "string"
}
],
"product_id": 0
}
]
}

复制
收回全部
回复范例
200default
Content type
application/json
{
"products": [
{
"error": "string",
"exemplars": [
{
"errors": [
"string"
],
"gtd": "string",
"marks": [
{
"errors": [
"string"
],
"mark": "string",
"mark_type": "string",
"valid": true
}
],
"rnpt": "string",
"valid": true
}
],
"product_id": 0,
"valid": true
}
]
}

复制
收回全部
Обновить данные экземпляров
post
/v1/fbs/posting/product/exemplar/update

描述和范例控制台
请使用 /v6/fbs/posting/product/exemplar/set, 方法，在传输实例数据后调用该方法，以保存“等待发运”状态下订单的最新实例数据。

您可以在 讨论 的评论中对此方法提供反馈 在 Ozon for dev 开发者社区中。

header Parameters
Client-Id
required
string
用户识别号。

Api-Key
required
string
API-密钥。

Request Body schema: application/json
posting_number
required
string
发货号。

回复
200数据已更新
default错误
请求范例
Content type
application/json
{
"posting_number": "string"
}

复制
收回全部
回复范例
default
Content type
application/json
{
"code": 0,
"details": [
{
"typeUrl": "string",
"value": "string"
}
],
"message": "string"
}

复制
收回全部
评价管理
对评价留下评论
post
/v1/review/comment/create

描述和范例控制台
仅适用于拥有 评价管理 或 Premium Pro 订阅的卖家。

您可以在开发者社区 Ozon for dev 的讨论区中，留下对此方法的反馈。

Request Body schema: application/json
mark_review_as_processed	
boolean
更新评论状态：

true — 状态将变更为 Processed（已处理）；
false — 状态不变。
parent_comment_id	
string
父级评论的标识符（您要回复的评论）。

review_id
required
string
评价标识符。

text
required
string
评论内容。

回复
200评论已创建
Response Schema: application/json
comment_id	
string
评论标识符。

default错误
请求范例
Content type
application/json
{
"mark_review_as_processed": true,
"parent_comment_id": "string",
"review_id": "string",
"text": "string"
}

复制
收回全部
回复范例
200default
Content type
application/json
{
"comment_id": "string"
}

复制
收回全部
删除对评价的评论
post
/v1/review/comment/delete

描述和范例控制台
仅适用于拥有 评价管理 或 Premium Pro 订阅的卖家。

您可以在开发者社区 Ozon for dev 的讨论区中，留下对此方法的反馈。

Request Body schema: application/json
comment_id
required
string
评论标识符。

回复
200评论已删除
Response Schema: application/json
object
default错误
请求范例
Content type
application/json
{
"comment_id": "string"
}

复制
收回全部
回复范例
200default
Content type
application/json
{ }

复制
收回全部
评价的评论列表
post
/v1/review/comment/list

描述和范例控制台
仅适用于拥有 评价管理 或 Premium Pro 订阅的卖家。

您可以在开发者社区 Ozon for dev 的讨论区中，留下对此方法的反馈。

该方法返回已通过审核的评价评论信息。

Request Body schema: application/json
limit
required
integer <int32>
限制回复中的值数量。 最少 — 20；最多 — 100。

offset	
integer <int32>
从列表开头跳过的元素数量：例如，如果 offset = 10，那么回复将从找到的第11个元素开始。

review_id
required
string
评价标识符。

sort_dir	
string
Default: "ASC"
Enum: "ASC" "DESC"
排序方向：

ASC — 按升序。
DESC — 按降序。
回复
200评价评论的信息
Response Schema: application/json
comments	
Array of objects
评论信息。

offset	
integer <int32>
搜索结果中的元素数量。

default错误
请求范例
Content type
application/json
{
"limit": 100,
"offset": 0,
"review_id": "0187310a-97d9-dfcf-3039-82d809f0e233",
"sort_dir": "ASC"
}

复制
收回全部
回复范例
200default
Content type
application/json
{
"comments": [
{
"id": "string",
"is_official": true,
"is_owner": true,
"parent_comment_id": "string",
"published_at": "2019-08-24T14:15:22Z",
"text": "string"
}
],
"offset": 0
}

复制
收回全部
更改评价状态
post
/v1/review/change-status

描述和范例控制台
仅适用于拥有 评价管理 或 Premium Pro 订阅的卖家。

您可以在开发者社区 Ozon for dev 的讨论区中，留下对此方法的反馈。

Request Body schema: application/json
review_ids
required
Array of strings
包含评价标识符的数组（数量在1到100之间）。

status
required
string
评价状态：

PROCESSED — 已处理。
UNPROCESSED — 未处理。
回复
200状态已更改
Response Schema: application/json
object
default错误
请求范例
Content type
application/json
{
"review_ids": [
"string"
],
"status": "string"
}

复制
收回全部
回复范例
200default
Content type
application/json
{ }

复制
收回全部
根据状态统计的评价数量
post
/v1/review/count

描述和范例控制台
仅适用于拥有 评价管理 或 Premium Pro 订阅的卖家。

您可以在开发者社区 Ozon for dev 的讨论区中，留下对此方法的反馈。

Request Body schema: application/json
object
回复
200处理和未处理评价的数量
Response Schema: application/json
processed	
integer <int32>
已处理评价的数量。

total	
integer <int32>
评价的总数量。

unprocessed	
integer <int32>
未处理评价的数量。

default错误
请求范例
Content type
application/json
{ }

复制
收回全部
回复范例
200default
Content type
application/json
{
"processed": 0,
"total": 0,
"unprocessed": 0
}

复制
收回全部
获取评价信息
post
/v1/review/info

描述和范例控制台
仅适用于拥有 评价管理 或 Premium Pro 订阅的卖家。

您可以在开发者社区 Ozon for dev 的讨论区中，留下对此方法的反馈。

Request Body schema: application/json
review_id
required
string
评价标识符。

回复
200评价信息
Response Schema: application/json
comments_amount	
integer <int32>
评价的回复数量。

dislikes_amount	
integer <int32>
评价的踩数量。

id	
string
评价标识符。

is_rating_participant	
boolean
true：评论是由官方人员留下的；false：评论是由买家留下的。

likes_amount	
integer <int32>
评价的点赞数量。

order_status	
string
买家留下评价的订单状态：

DELIVERED— 已送达，
CANCELLED — 已取消。
photos	
Array of objects
图片信息。

photos_amount	
integer <int32>
评价中的图片数量。

published_at	
string <date-time>
评价的发布日期。

rating	
integer <int32>
评价评分。

sku	
integer <int64>
Ozon系统中的商品识别符——SKU。

status	
string
评价状态：

UNPROCESSED — 未处理，
PROCESSED — 已处理。
text	
string
评价文字。

videos	
Array of objects
视频信息。

videos_amount	
integer <int32>
评价中的视频数量。

default错误
请求范例
Content type
application/json
{
"review_id": "string"
}

复制
收回全部
回复范例
200default
Content type
application/json
{
"comments_amount": 0,
"dislikes_amount": 0,
"id": "string",
"is_rating_participant": true,
"likes_amount": 0,
"order_status": "string",
"photos": [
{
"height": 0,
"url": "string",
"width": 0
}
],
"photos_amount": 0,
"published_at": "2019-08-24T14:15:22Z",
"rating": 0,
"sku": 0,
"status": "string",
"text": "string",
"videos": [
{
"height": 0,
"preview_url": "string",
"short_video_preview_url": "string",
"url": "string",
"width": 0
}
],
"videos_amount": 0
}

复制
收回全部
获取评价列表
post
/v2/review/list

描述和范例控制台
适用于已开通“评价管理”或Premium Pro订阅的卖家。

您可以在讨论的评论中对此方法提供反馈 在 Ozon for dev 开发者社区中。

header Parameters
Client-Id
required
string
用户识别号。

Api-Key
required
string
API-密钥。

Request Body schema: application/json
filters	
object
用于搜索评价的筛选条件。

last_id	
string
响应中最后一条评价的标识符。

limit
required
integer <int32> [ 20 .. 100 ]
响应中的评价数量。

sort_dir	
string
Enum: "ASC" "DESC"
排序方向：

ASC——升序；
DESC——降序。
回复
200评价列表
Response Schema: application/json
has_next	
boolean
true，表示响应中未返回全部评价。

last_id	
string
页面中最后一个评价的标识符。

reviews	
Array of objects
评价列表。

400参数错误
403访问被拒绝
404响应未找到
409请求冲突
500服务器内部错误
请求范例
Content type
application/json
{
"filters": {
"sku": [
0
],
"order_status": "NEW",
"status": "DELIVERED",
"published_from": "2026-03-10T14:08:00.257Z",
"published_to": "2026-03-10T14:08:00.257Z"
},
"last_id": "string",
"limit": 0,
"sort_dir": "ASC"
}

复制
收回全部
回复范例
200400403404409500
Content type
application/json
{
"reviews": [
{
"id": "017c0d1c-66d3-b838-3d29-cf9b95a6ac48",
"sku": "148591503",
"text": "string",
"published_at": "2024-10-10T07:23:55.970Z",
"rating": 2,
"status": "UNPROCESSED",
"comments_amount": 0,
"photos_amount": 0,
"videos_amount": 0,
"order_status": "DELIVERED",
"is_rating_participant": true
}
],
"has_next": true,
"last_id": "017c0d53-a7c8-81ef-53de-7d32fcbd7421"
}

复制
收回全部
获取评价列表 Deprecated
post
/v1/review/list

描述和范例控制台
该方法已弃用。请切换到/v2/review/list。
仅适用于拥有 评价管理 或 Premium Pro 订阅的卖家。

您可以在开发者社区 Ozon for dev 的讨论区中，留下对此方法的反馈。

该方法不会返回商品评价中的“优点”和“缺点”参数（如果有）。 这些参数已过时，新的评价中不再包含这些参数。

Request Body schema: application/json
last_id	
string
页面中最后一个评价的标识符。

limit
required
integer <int32>
限制回复中的值数量。最少 — 20；最多 — 100。

sort_dir	
string
排序方向：

ASC — 按升序。
DESC — 按降序。
status	
string
评价状态：

ALL — 全部，
UNPROCESSED — 未处理的，
PROCESSED — 已处理的。
回复
200评价列表
Response Schema: application/json
has_next	
boolean
true：回复中未返回所有评价。

last_id	
string
页面中最后一个评价的标识符。

reviews	
Array of objects
评价信息。

default错误
请求范例
Content type
application/json
{
"last_id": "",
"limit": 100,
"sort_dir": "ASC",
"status": "ALL"
}

复制
收回全部
回复范例
200default
Content type
application/json
{
"has_next": true,
"last_id": "string",
"reviews": [
{
"comments_amount": 0,
"id": "string",
"is_rating_participant": true,
"order_status": "string",
"photos_amount": 0,
"published_at": "2019-08-24T14:15:22Z",
"rating": 0,
"sku": 0,
"status": "string",
"text": "string",
"videos_amount": 0
}
]
}

复制
收回全部
问题和回答管理
创建对问题的回答
post
/v1/question/answer/create

描述和范例控制台
仅对已订阅 Premium Plus 的卖家开放。

您可以在开发者社区 Ozon for dev 的讨论评论区中，对该方法留下反馈。

header Parameters
Client-Id
required
string
用户识别号。

Api-Key
required
string
API-密钥。

Request Body schema: application/json
question_id
required
string
问题标识符。

sku
required
integer <int64>
Ozon 系统中的商品标识符——SKU。

text
required
string
回答文本，长度为 2 至 3000 个字符。

回复
200问题回答标识符
Response Schema: application/json
answer_id	
string
问题回答标识符。

default错误
请求范例
Content type
application/json
{
"question_id": "string",
"sku": 0,
"text": "string"
}

复制
收回全部
回复范例
200default
Content type
application/json
{
"answer_id": "0192e7ce-e12c-7a74-afc7-26e877799204"
}

复制
收回全部
删除问题回答
post
/v1/question/answer/delete

描述和范例控制台
仅对已订阅 Premium Plus 的卖家开放。

您可以在开发者社区 Ozon for dev 的讨论评论区中，对该方法留下反馈。

header Parameters
Client-Id
required
string
用户识别号。

Api-Key
required
string
API-密钥。

Request Body schema: application/json
answer_id
required
string
回答标识符。

sku
required
integer <int64>
Ozon 系统中的商品标识符——SKU。

回复
200回答已删除
default错误
请求范例
Content type
application/json
{
"answer_id": "0192e7ce-e12c-7a74-afc7-26e877799204",
"sku": 646399170
}

复制
收回全部
回复范例
default
Content type
application/json
{
"code": 0,
"details": "string",
"message": "string"
}

复制
收回全部
问题回答列表
post
/v1/question/answer/list

描述和范例控制台
仅对已订阅 Premium Plus 的卖家开放。

您可以在开发者社区 Ozon for dev 的讨论评论区中，对该方法留下反馈。

header Parameters
Client-Id
required
string
用户识别号。

Api-Key
required
string
API-密钥。

Request Body schema: application/json
last_id	
页面上最后一个值的标识符。

如果是首次请求，请将该字段留空。 后续请求中，请传入上一次请求返回的 last_id。

question_id
required
string
问题标识符。

sku
required
integer <int64>
Ozon 系统中的商品标识符——SKU。

回复
200问题回答列表
Response Schema: application/json
answers	
Array of objects
回答。

last_id	
string
页面上最后一个值的标识符。

要获取下一个批次的数据，请在下一个请求的 last_id 参数中传递上次获取的值。

default错误
请求范例
Content type
application/json
{
"last_id": "",
"question_id": "019228a7-91d8-76af-a73a-e989dfac7ac8",
"sku": 646399170
}

复制
收回全部
回复范例
200default
Content type
application/json
{
"answers": [
{
"author_name": "string",
"id": "string",
"published_at": "2024-08-14T11:44:35.352Z",
"question_id": "string",
"sku": 646399170,
"status_publication": "",
"text": "string"
}
],
"last_id": "string"
}

复制
收回全部
更改问题状态
post
/v1/question/change_status

描述和范例控制台
仅对已订阅 Premium Plus 的卖家开放。

您可以在开发者社区 Ozon for dev 的讨论评论区中，对该方法留下反馈。

header Parameters
Client-Id
required
string
用户识别号。

Api-Key
required
string
API-密钥。

Request Body schema: application/json
question_ids
required
Array of strings
问题标识符。

status
required
string
问题状态：

NEW——新的，
VIEWED——已查看，
PROCESSED——已处理。
回复
200状态已更改
default错误
请求范例
Content type
application/json
{
"question_ids": [
"string"
],
"status": "VIEWED"
}

复制
收回全部
回复范例
default
Content type
application/json
{
"code": 0,
"details": "string",
"message": "string"
}

复制
收回全部
按状态统计问题数量
post
/v1/question/count

描述和范例控制台
仅对已订阅 Premium Plus 的卖家开放。

您可以在开发者社区 Ozon for dev 的讨论评论区中，对该方法留下反馈。

header Parameters
Client-Id
required
string
用户识别号。

Api-Key
required
string
API-密钥。

回复
200按状态统计问题数量
Response Schema: application/json
all	
integer <int64>
问题总数。

new	
integer <int64>
新问题数量。

processed	
integer <int64>
已处理问题数量。

unprocessed	
integer <int64>
未处理问题数量。

viewed	
integer <int64>
已查看问题数量。

default错误
回复范例
200default
Content type
application/json
{
"all": 10,
"new": 3,
"processed": 4,
"unprocessed": 1,
"viewed": 1
}

复制
收回全部
问题详情
post
/v1/question/info

描述和范例控制台
仅对已订阅 Premium Plus 的卖家开放。

您可以在开发者社区 Ozon for dev 的讨论评论区中，对该方法留下反馈。

header Parameters
Client-Id
required
string
用户识别号。

Api-Key
required
string
API-密钥。

Request Body schema: application/json
question_id
required
string
问题标识符。

回复
200问题详情
Response Schema: application/json
answers_count	
integer <int64>
问题的回答数量。

author_name	
string
问题作者。

id	
string
问题标识符。

product_url	
string
商品链接。

published_at	
timestamp
问题发布日期。

question_link	
string
问题链接。

sku	
integer <int64>
Ozon 系统中的商品标识符——SKU。

status	
enum
问题状态：

NEW——新的，
ALL——全部问题，
VIEWED——已查看，
PROCESSED——已处理，
UNPROCESSED——未处理。
text	
string
问题文本。

default错误
请求范例
Content type
application/json
{
"question_id": "string"
}

复制
收回全部
回复范例
200default
Content type
application/json
{
"answers_count": "0",
"author_name": "string",
"product_url": "https://www.ozon.ru/product/149829950/",
"sku": 646399170,
"id": "0192a009-769f-7ee9-b412-893045171a66",
"text": "string",
"question_link": "https://www.ozon.ru/product/149829950/questions/?qid=290125772&utm_campaign=reviews_sc_link&utm_medium=share_button&utm_source=smm",
"published_at": "2024-10-08T10:09:29.099284Z",
"status": "VIEWED"
}

复制
收回全部
问题列表
post
/v1/question/list

描述和范例控制台
仅对已订阅 Premium Plus 的卖家开放。

您可以在开发者社区 Ozon for dev 的讨论评论区中，对该方法留下反馈。

header Parameters
Client-Id
required
string
用户识别号。

Api-Key
required
string
API-密钥。

Request Body schema: application/json
filter	
object
筛选器。

last_id	
string
页面上最后一个值的ID。运行第一个查询时，将此字段留空。

要检索以下数值，请从上一个查询的响应中指定last_id。

limit	
integer <int64> <= 100
响应中返回的值数量。

sort_dir	
string
Default: "DESC"
Enum: "DESC" "ASC"
排序方向：

DESC——降序；
ASC——升序。
回复
200问题列表
Response Schema: application/json
questions	
Array of objects [ 0 .. 10 ] items
问题。

last_id	
string
页面上最后一个值的标识符。

要获取下一个批次的数据，请在下一个请求的 last_id 参数中传递上次获取的值。

has_next	
boolean
如果响应中未返回所有问题，则为true。

default错误
请求范例
Content type
application/json
{
"filter": {
"date_from": "2019-08-24T14:15:22Z",
"date_to": "2019-08-24T14:15:22Z",
"status": "ALL"
},
"limit": 100,
"last_id": "",
"sort_dir": "ASC"
}

复制
收回全部
回复范例
200default
Content type
application/json
{
"has_next": true,
"questions": [
{
"answers_count": 1,
"author_name": "string",
"id": "019294ff-6888-7009-89d8-26569e4e450d",
"sku": 646399170,
"product_url": "https://www.ozon.ru/product/1649246352/",
"published_at": "2024-08-14T12:02:01.889Z",
"question_link": "https://www.ozon.ru/product/1649246352/questions/?qid=290180206&utm_campaign=reviews_sc_link&utm_medium=share_button&utm_source=smm",
"text": "string",
"status": "PROCESSED"
}
],
"last_id": "019228a7-91d8-76af-a73a-e989dfac7ac8"
}

复制
收回全部
提问数量最多的商品
post
/v1/question/top_sku

描述和范例控制台
仅对已订阅 Premium Plus 的卖家开放。

您可以在开发者社区 Ozon for dev 的讨论评论区中，对该方法留下反馈。

header Parameters
Client-Id
required
string
用户识别号。

Api-Key
required
string
API-密钥。

Request Body schema: application/json
limit
required
integer <int64> [ 1 .. 100 ]
响应结果数量。

回复
200商品标识符
Response Schema: application/json
sku	
Array of strings <int64>
Ozon 系统中的商品标识符（SKU）列表。

default错误
请求范例
Content type
application/json
{
"limit": "100"
}

复制
收回全部
回复范例
200default
Content type
application/json
{
"sku": [
56371271
]
}

复制
收回全部
卖家促销活动
如何管理卖家促销活动

在卖家知识库中了解更多关于卖家促销活动的信息

创建采用"折扣"机制的促销活动
post
/v1/seller-actions/create/discount

描述和范例控制台
您可以在Ozon for dev开发者社区的评论区对该方法的使用情况留下反馈。

header Parameters
Client-Id
required
string
用户识别号。

Api-Key
required
string
API-密钥。

Request Body schema: application/json
date_end
required
string <date-time>
促销活动结束日期与时间。

date_start
required
string <date-time>
促销活动开始日期与时间。

min_action_percent
required
number <double>
最低折扣百分比。

title	
string [ 1 .. 256 ] characters
促销活动名称。

回复
200促销活动已创建
Response Schema: application/json
action_id	
integer <uint64>
促销活动标识符。

default错误
请求范例
Content type
application/json
{
"date_end": "2019-08-24T14:15:22Z",
"date_start": "2019-08-24T14:15:22Z",
"min_action_percent": 0,
"title": "string"
}

复制
收回全部
回复范例
200default
Content type
application/json
{
"action_id": 0
}

复制
收回全部
创建采用"基于订单总额的折扣"机制的促销活动
post
/v1/seller-actions/create/discount-with-condition

描述和范例控制台
您可以在Ozon for dev开发者社区的评论区对该方法的使用情况留下反馈。

header Parameters
Client-Id
required
string
用户识别号。

Api-Key
required
string
API-密钥。

Request Body schema: application/json
date_end
required
string <date-time>
促销活动结束日期与时间。

date_start
required
string <date-time>
促销活动开始日期与时间。

discount_type
required
string
Enum: "PERCENT" "CURRENCY"
折扣类型：

PERCENT——百分比折扣；
CURRENCY——按金额折扣。
discount_value
required
number <float>
折扣幅度。

min_order_amount
required
number <double>
折扣生效的订单金额门槛。

title	
string [ 1 .. 256 ] characters
促销活动名称。

回复
200促销活动已创建
Response Schema: application/json
action_id	
integer <uint64>
促销活动标识符。

default错误
请求范例
Content type
application/json
{
"date_end": "2019-08-24T14:15:22Z",
"date_start": "2019-08-24T14:15:22Z",
"discount_type": "PERCENT",
"discount_value": 0,
"min_order_amount": 0,
"title": "string"
}

复制
收回全部
回复范例
200default
Content type
application/json
{
"action_id": 0
}

复制
收回全部
创建采用"免息分期付款"机制的促销活动
post
/v1/seller-actions/create/installment

描述和范例控制台
分期付款周期为6个月。

您可以在Ozon for dev开发者社区的评论区对该方法的使用情况留下反馈。

header Parameters
Client-Id
required
string
用户识别号。

Api-Key
required
string
API-密钥。

Request Body schema: application/json
date_start
required
string <date-time>
促销活动开始日期与时间。

title
required
string [ 1 .. 256 ] characters
促销活动名称。

回复
200促销活动已创建
Response Schema: application/json
action_id	
integer <uint64>
促销活动标识符。

default错误
请求范例
Content type
application/json
{
"date_start": "2019-08-24T14:15:22Z",
"title": "string"
}

复制
收回全部
回复范例
200default
Content type
application/json
{
"action_id": 0
}

复制
收回全部
创建采用"多级满额折扣"机制的促销活动
post
/v1/seller-actions/create/multi-level-discount

描述和范例控制台
商品将自动加入促销活动，无需使用方法/v1/seller-actions/products/add。

您可以在Ozon for dev开发者社区的评论区对该方法的使用情况留下反馈。

header Parameters
Client-Id
required
string
用户识别号。

Api-Key
required
string
API-密钥。

Request Body schema: application/json
date_end
required
string <date-time>
促销活动结束日期与时间。

date_start
required
string <date-time>
促销活动开始日期与时间。

discount_levels
required
Array of objects [ 2 .. 4 ] items
折扣等级。

discount_type
required
string
Enum: "PERCENT" "CURRENCY"
折扣类型：

PERCENT——百分比折扣；
CURRENCY——按金额折扣。
is_legal_entities_segment	
boolean
true，表示促销活动仅面向法人实体。

title	
string [ 1 .. 256 ] characters
促销活动名称。

回复
200促销活动已创建
Response Schema: application/json
action_id	
integer <uint64>
促销活动标识符。

default错误
请求范例
Content type
application/json
{
"date_end": "2019-08-24T14:15:22Z",
"date_start": "2019-08-24T14:15:22Z",
"discount_levels": [
{
"discount_value": 0,
"order_amount": 0
},
{
"discount_value": 0,
"order_amount": 0
}
],
"discount_type": "PERCENT",
"is_legal_entities_segment": true,
"title": "string"
}

复制
收回全部
回复范例
200default
Content type
application/json
{
"action_id": 0
}

复制
收回全部
创建采用"促销码折扣"机制的促销活动
post
/v1/seller-actions/create/voucher

描述和范例控制台
您可以在Ozon for dev开发者社区的评论区对该方法的使用情况留下反馈。

header Parameters
Client-Id
required
string
用户识别号。

Api-Key
required
string
API-密钥。

Request Body schema: application/json
budget
required
integer <int64>
促销活动预算。预算用尽后，促销活动将停止。

date_end
required
string <date-time>
促销活动结束日期与时间。

date_start
required
string <date-time>
促销活动开始日期与时间。

discount_type
required
string
Enum: "PERCENT" "CURRENCY"
折扣类型：

PERCENT——百分比折扣；
CURRENCY——按金额折扣。
discount_value
required
number <double>
折扣幅度。

title
required
string [ 1 .. 256 ] characters
促销活动名称。

user_ids	
Array of strings <uint64> <= 50
可使用该促销码的用户标识符列表。

voucher_parameters
required
object
促销码参数。

回复
200促销活动已创建
Response Schema: application/json
action_id	
integer <uint64>
促销活动标识符。

default错误
请求范例
Content type
application/json
{
"budget": 0,
"date_end": "2019-08-24T14:15:22Z",
"date_start": "2019-08-24T14:15:22Z",
"discount_type": "PERCENT",
"discount_value": 0,
"title": "string",
"user_ids": [
"string"
],
"voucher_parameters": {
"count_codes": 0,
"is_private": true,
"type": "ONE"
}
}

复制
收回全部
回复范例
200default
Content type
application/json
{
"action_id": 0
}

复制
收回全部
更新“折扣”机制的促销活动
post
/v1/seller-actions/update/discount

描述和范例控制台
不适用于独联体地区卖家。
您可以在开发者社区 Ozon for dev 的讨论评论区中，留下对此方法的反馈。

header Parameters
Client-Id
required
string
用户识别号。

Api-Key
required
string
API-密钥。

Request Body schema: application/json
action_id	
integer <uint64>
促销活动标识符。

action_parameters	
object
促销活动参数。

回复
200促销活动已更新
400参数有误
403访问拒绝
404无响应
409请求冲突
500服务器内部错误
请求范例
Content type
application/json
{
"action_id": 0,
"action_parameters": {
"date_end": "2019-08-24T14:15:22Z",
"date_start": "2019-08-24T14:15:22Z",
"title": "string"
}
}

复制
收回全部
回复范例
400403404409500
Content type
application/json
{
"code": 0,
"details": [
{
"typeUrl": "string",
"value": "string"
}
],
"message": "string"
}

复制
收回全部
更新“基于订单总额的折扣”机制的促销活动
post
/v1/seller-actions/update/discount-with-condition

描述和范例控制台
您可以在开发者社区 Ozon for dev 的讨论评论区中，留下对此方法的反馈。

header Parameters
Client-Id
required
string
用户识别号。

Api-Key
required
string
API-密钥。

Request Body schema: application/json
action_id	
integer <uint64>
促销活动标识符。

action_parameters	
object
促销活动参数。

回复
200促销活动已更新
400参数有误
403访问拒绝
404无响应
409请求冲突
500服务器内部错误
请求范例
Content type
application/json
{
"action_id": 0,
"action_parameters": {
"date_end": "2019-08-24T14:15:22Z",
"date_start": "2019-08-24T14:15:22Z",
"discount_value": 0,
"min_order_amount": 0,
"title": "string"
}
}

复制
收回全部
回复范例
400403404409500
Content type
application/json
{
"code": 0,
"details": [
{
"typeUrl": "string",
"value": "string"
}
],
"message": "string"
}

复制
收回全部
更新“免息分期付款”机制的促销活动
post
/v1/seller-actions/update/installment

描述和范例控制台
分期付款周期为6个月。

您可以在开发者社区 Ozon for dev 的讨论评论区中，留下对此方法的反馈。

header Parameters
Client-Id
required
string
用户识别号。

Api-Key
required
string
API-密钥。

Request Body schema: application/json
action_id	
integer <uint64>
促销活动标识符。

action_parameters	
object
促销活动参数。

回复
200促销活动已更新
400参数有误
403访问拒绝
404无响应
409请求冲突
500服务器内部错误
请求范例
Content type
application/json
{
"action_id": 0,
"action_parameters": {
"date_start": "2019-08-24T14:15:22Z",
"title": "string"
}
}

复制
收回全部
回复范例
400403404409500
Content type
application/json
{
"code": 0,
"details": [
{
"typeUrl": "string",
"value": "string"
}
],
"message": "string"
}

复制
收回全部
更新“多级满额折扣”机制的促销活动
post
/v1/seller-actions/update/multi-level-discount

描述和范例控制台
商品将自动加入促销活动，无需调用方法/v1/seller-actions/products/add。

您可以在开发者社区 Ozon for dev 的讨论评论区中，留下对此方法的反馈。

header Parameters
Client-Id
required
string
用户识别号。

Api-Key
required
string
API-密钥。

Request Body schema: application/json
action_id	
integer <uint64>
促销活动标识符。

action_parameters	
object
促销活动参数。

回复
200促销活动已更新
400参数有误
403访问拒绝
404无响应
409请求冲突
500服务器内部错误
请求范例
Content type
application/json
{
"action_id": 0,
"action_parameters": {
"date_end": "2019-08-24T14:15:22Z",
"date_start": "2019-08-24T14:15:22Z",
"discount_levels": [
{
"discount_value": 0,
"order_amount": 0
},
{
"discount_value": 0,
"order_amount": 0
}
],
"is_legal_entities_segment": true,
"title": "string"
}
}

复制
收回全部
回复范例
400403404409500
Content type
application/json
{
"code": 0,
"details": [
{
"typeUrl": "string",
"value": "string"
}
],
"message": "string"
}

复制
收回全部
更新“促销码折扣”机制的促销活动
post
/v1/seller-actions/update/voucher

描述和范例控制台
您可以在开发者社区 Ozon for dev 的讨论评论区中，留下对此方法的反馈。

header Parameters
Client-Id
required
string
用户识别号。

Api-Key
required
string
API-密钥。

Request Body schema: application/json
action_id	
integer <uint64>
促销活动标识符。

action_parameters	
object
促销活动参数。

回复
200促销活动已更新
400参数有误
403访问拒绝
404无响应
409请求冲突
500服务器内部错误
请求范例
Content type
application/json
{
"action_id": 0,
"action_parameters": {
"budget": 0,
"date_end": "2019-08-24T14:15:22Z",
"date_start": "2019-08-24T14:15:22Z",
"discount_value": 0,
"title": "string",
"user_ids": [
"string"
]
}
}

复制
收回全部
回复范例
400403404409500
Content type
application/json
{
"code": 0,
"details": [
{
"typeUrl": "string",
"value": "string"
}
],
"message": "string"
}

复制
收回全部
将商品添加到促销活动中
post
/v1/seller-actions/products/add

描述和范例控制台
您可以在Ozon for dev开发者社区的评论区对该方法的使用情况留下反馈。

header Parameters
Client-Id
required
string
用户识别号。

Api-Key
required
string
API-密钥。

Request Body schema: application/json
action_id
required
integer <uint64>
促销活动标识符。请通过方法/v1/seller-actions/list获取该参数的值。

products
required
Array of objects <= 100 items
商品信息。

回复
200商品已添加
default错误
请求范例
Content type
application/json
{
"action_id": 0,
"products": [
{
"currency": "RUB",
"discount_percent": 0,
"sku": 0
}
]
}

复制
收回全部
回复范例
default
Content type
application/json
{
"code": 0,
"details": [
{
"typeUrl": "string",
"value": "string"
}
],
"message": "string"
}

复制
收回全部
获取促销活动可用商品列表
post
/v1/seller-actions/products/candidates

描述和范例控制台
您可以在Ozon for dev开发者社区的评论区对该方法的使用情况留下反馈。

header Parameters
Client-Id
required
string
用户识别号。

Api-Key
required
string
API-密钥。

Request Body schema: application/json
action_id
required
integer <uint64>
促销活动标识符。请通过方法/v1/seller-actions/list获取该参数的值。

cursor	
integer <uint64>
用于选择下一批数据的指针。

limit
required
integer <int64> [ 1 .. 100 ]
Default: 100
响应中的最大元素数量。

回复
200商品列表
Response Schema: application/json
cursor	
integer <uint64>
用于选择下一批数据的指针。

has_next	
boolean
响应中仅返回了部分值的标志：

true——请使用新的cursor参数重复请求，以获取其余值；
false——响应中已包含所有值。
products	
Array of objects
商品信息。

default错误
请求范例
Content type
application/json
{
"action_id": 0,
"cursor": 0,
"limit": 100
}

复制
收回全部
回复范例
200default
Content type
application/json
{
"cursor": 0,
"has_next": true,
"products": [
{
"action_price": 0,
"base_price": 0,
"currency": "string",
"discount_percent": 0,
"is_active": true,
"min_seller_price": 0,
"name": "string",
"offer_id": "string",
"price": 0,
"product_id": 0,
"quant_size": 0,
"quant_type": "UNSPECIFIED",
"sku": [
"string"
]
}
]
}

复制
收回全部
从促销活动中移除商品
post
/v1/seller-actions/products/delete

描述和范例控制台
您可以在Ozon for dev开发者社区的评论区对该方法的使用情况留下反馈。

header Parameters
Client-Id
required
string
用户识别号。

Api-Key
required
string
API-密钥。

Request Body schema: application/json
action_id
required
integer <uint64>
促销活动标识符。请通过方法/v1/seller-actions/list获取该参数的值。

skus
required
Array of strings <uint64> <= 100 items
Ozon系统中的商品标识符——SKU。

回复
200商品已移除
default错误
请求范例
Content type
application/json
{
"action_id": 0,
"skus": [
"string"
]
}

复制
收回全部
回复范例
default
Content type
application/json
{
"code": 0,
"details": [
{
"typeUrl": "string",
"value": "string"
}
],
"message": "string"
}

复制
收回全部
获取参与活动的商品列表
post
/v1/seller-actions/products/list

描述和范例控制台
您可以在Ozon for dev开发者社区的评论区对该方法的使用情况留下反馈。

header Parameters
Client-Id
required
string
用户识别号。

Api-Key
required
string
API-密钥。

Request Body schema: application/json
action_id
required
integer <uint64>
促销活动标识符。请通过方法/v1/seller-actions/list获取该参数的值。

cursor	
integer <uint64>
用于选择下一批数据的指针。

limit
required
integer <int64> [ 1 .. 100 ]
Default: 100
响应中的最大元素数量。

回复
200商品列表
Response Schema: application/json
cursor	
integer <uint64>
用于选择下一批数据的指针。

has_next	
boolean
响应中仅返回了部分值的标志：

true——请使用新的cursor参数重复请求，以获取其余值；
false——响应中已包含所有值。
products	
Array of objects
商品信息。

default错误
请求范例
Content type
application/json
{
"action_id": 0,
"cursor": 0,
"limit": 100
}

复制
收回全部
回复范例
200default
Content type
application/json
{
"cursor": 0,
"has_next": true,
"products": [
{
"action_price": 0,
"base_price": 0,
"currency": "string",
"discount_percent": 0,
"is_active": true,
"min_seller_price": 0,
"name": "string",
"offer_id": "string",
"price": 0,
"product_id": 0,
"quant_size": 0,
"quant_type": "UNSPECIFIED",
"sku": [
"string"
]
}
]
}

复制
收回全部
将促销活动归档
post
/v1/seller-actions/archive

描述和范例控制台
您可以在Ozon for dev开发者社区的评论区对该方法的使用情况留下反馈。

header Parameters
Client-Id
required
string
用户识别号。

Api-Key
required
string
API-密钥。

Request Body schema: application/json
action_id
required
integer <uint64>
促销活动标识符。请通过方法/v1/seller-actions/list获取该参数的值。

回复
200促销活动已归档
default错误
请求范例
Content type
application/json
{
"action_id": 0
}

复制
收回全部
回复范例
default
Content type
application/json
{
"code": 0,
"details": [
{
"typeUrl": "string",
"value": "string"
}
],
"message": "string"
}

复制
收回全部
启用或关闭活动
post
/v1/seller-actions/change-activity

描述和范例控制台
您可以在Ozon for dev开发者社区的评论区对该方法的使用情况留下反馈。

header Parameters
Client-Id
required
string
用户识别号。

Api-Key
required
string
API-密钥。

Request Body schema: application/json
action_id
required
integer <uint64>
促销活动标识符。请通过方法/v1/seller-actions/list获取该参数的值。

is_turn_on
required
boolean
true，用于启用促销活动。

回复
200成功
default错误
请求范例
Content type
application/json
{
"action_id": 0,
"is_turn_on": true
}

复制
收回全部
回复范例
default
Content type
application/json
{
"code": 0,
"details": [
{
"typeUrl": "string",
"value": "string"
}
],
"message": "string"
}

复制
收回全部
获取促销活动列表
post
/v1/seller-actions/list

描述和范例控制台
您可以在Ozon for dev开发者社区的评论区对该方法的使用情况留下反馈。

header Parameters
Client-Id
required
string
用户识别号。

Api-Key
required
string
API-密钥。

Request Body schema: application/json
action_ids	
Array of strings <uint64> <= 100 items
促销活动标识符列表。

action_type	
Array of strings
Items Enum: "DISCOUNT" "VOUCHER_DISCOUNT" "DISCOUNT_WITH_CONDITION" "INSTALLMENT" "INDIVIDUAL_DISCOUNT_BY_PRODUCTS" "OZON_ACCOUNT_DISCOUNT" "MULTI_LEVEL_DISCOUNT_ON_AMOUNT"
促销活动机制：

DISCOUNT——折扣；
VOUCHER_DISCOUNT——促销码折扣；
DISCOUNT_WITH_CONDITION——基于订单总额的折扣；
INSTALLMENT——免息分期付款；
INDIVIDUAL_DISCOUNT_BY_PRODUCTS——卖家积分；
OZON_ACCOUNT_DISCOUNT——Ozon银行卡专享额外折扣；
MULTI_LEVEL_DISCOUNT_ON_AMOUNT——多级满额折扣。
limit
required
integer <uint64> [ 1 .. 100 ]
每页显示的数量。

offset	
integer <uint64>
在响应中将被跳过的项目数量。例如，当offset = 10时，响应将从第11个找到的元素开始。

search	
string >= 3 characters
按促销活动名称搜索。

status	
Array of strings
Items Enum: "ACTIVE" "ENDED" "PLANNED" "PAUSED"
促销活动状态：

ACTIVE—— 活跃；
ENDED——已结束；
PLANNED——已计划；
PAUSED——已暂停。
回复
200促销活动列表
Response Schema: application/json
actions	
Array of objects
促销活动列表。

total	
integer <uint64>
促销活动总数。

default错误
请求范例
Content type
application/json
{
"action_ids": [
"string"
],
"action_type": [
"DISCOUNT"
],
"limit": 1,
"offset": 0,
"search": "string",
"status": [
"ACTIVE"
]
}

复制
收回全部
回复范例
200default
Content type
application/json
{
"actions": [
{
"action_id": 0,
"action_parameters": {
"addresses": [
"string"
],
"auto_stop_action_reason": "UNSPECIFIED",
"budget": 0,
"budget_spent": 0,
"date_end": "2019-08-24T14:15:22Z",
"date_start": "2019-08-24T14:15:22Z",
"discount_levels": [
{
"discount_value": 0,
"order_amount": 0
}
],
"discount_type": "UNSPECIFIED",
"discount_value": 0,
"is_legal_entities_segment": true,
"min_action_percent": 0,
"min_order_amount": 0,
"picked_segments": [
{
"segments": [
{
"description": "string",
"id": 0,
"name": "string",
"type": "UNSPECIFIED"
}
]
}
],
"status": "ACTIVE",
"title": "string",
"type": "DISCOUNT",
"voucher_parameters": {
"count_codes": 0,
"is_private": true,
"type": "UNSPECIFIED"
},
"warehouses": [
"string"
]
},
"allow_delete": true,
"highlight_url": "string",
"is_editable": true,
"is_participated": true,
"is_turn_on": true,
"sku_count": 0
}
],
"total": 0
}

复制
收回全部
获取CSV格式的促销码文件
post
/v1/seller-actions/voucher/get

描述和范例控制台
您可以在Ozon for dev开发者社区的评论区对该方法的使用情况留下反馈。

header Parameters
Client-Id
required
string
用户识别号。

Api-Key
required
string
API-密钥。

Request Body schema: application/json
action_id
required
integer <uint64>
促销活动标识符。请通过方法/v1/seller-actions/list获取该参数的值。

回复
200促销码文件
Response Schema: application/json
file	
string
促销码CSV文件链接。

default错误
请求范例
Content type
application/json
{
"action_id": 0
}

复制
收回全部
回复范例
200default
Content type
application/json
{
"file": "string"
}

复制
收回全部
Ozon促销活动
获取促销活动自动添加列表中的商品列表
post
/v1/actions/auto-add/products/list

描述和范例控制台
您可以在讨论的评论中对此方法提供反馈在 Ozon for dev 开发者社区中。

header Parameters
Client-Id
required
string
用户识别号。

Api-Key
required
string
API-密钥。

Request Body schema: application/json
action_id
required
integer <uint64>
促销活动标识符。

auto_add_date
required
string <date-time>
方法/v1/actions响应中result.auto_add_dates参数里的商品自动添加到促销活动中的日期和时间。

limit
required
integer <uint64> [ 1 .. 100 ]
响应中返回的值数量。

offset	
integer <uint64>
Default: 0
在响应中将被跳过的项目数量。例如，如果offset = 10，响应将从第11个找到的元素开始。

回复
200启用自动添加的商品列表
Response Schema: application/json
products	
Array of objects
启用自动添加的商品列表。

total	
integer <uint64>
商品总数。

400参数有误
403拒绝访问
404未找到答复
409请求冲突
500内部服务器出错
请求范例
Content type
application/json
{
"action_id": "250204",
"auto_add_date": "2035-08-28T14:00:00Z",
"limit": "1",
"offset": "0"
}

复制
收回全部
回复范例
200400403404409500
Content type
application/json
{
"products": [
{
"produсt_id": "14903",
"offer_id": "PS0007",
"sku": "146279508",
"name": "香薰 / 浴用油 / 精油 \"冷杉\"，250毫升",
"price": 114,
"max_discount_price": 79,
"min_seller_price:": 50,
"marketplace_seller_price": 59,
"action_price_to_auto_add": 79,
"min_action_quantity": "0",
"quantity_to_auto_add": "10",
"currency": "RUB",
"add_mode": "MANUAL"
}
],
"total": "443"
}

复制
收回全部
获取可自动添加到促销活动中的商品列表
post
/v1/actions/auto-add/products/candidates

描述和范例控制台
您可以在讨论的评论中对此方法提供反馈在 Ozon for dev 开发者社区中。

header Parameters
Client-Id
required
string
用户识别号。

Api-Key
required
string
API-密钥。

Request Body schema: application/json
action_id
required
integer <uint64>
促销活动标识符。

auto_add_date
required
string <date-time>
方法/v1/actions响应中result.auto_add_dates参数里的商品自动添加到促销活动中的日期和时间。

limit
required
integer <uint64> [ 1 .. 100 ]
响应中返回的值数量。

offset	
integer <uint64>
Default: 0
在响应中将被跳过的项目数量。例如，如果offset = 10，响应将从第11个找到的元素开始。

回复
200可自动添加到促销活动中的商品列表
Response Schema: application/json
products	
Array of objects
可用于自动添加到促销活动中的商品列表。

total	
integer <uint64>
商品总数。

400参数有误
403拒绝访问
404未找到答复
409请求冲突
500内部服务器出错
请求范例
Content type
application/json
{
"action_id": "250204",
"auto_add_date": "2035-08-28T14:00:00Z",
"limit": "1",
"offset": "0"
}

复制
收回全部
回复范例
200400403404409500
Content type
application/json
{
"products": [
{
"produсt_id": "14903",
"offer_id": "PS0007",
"sku": "146279508",
"name": "香薰 / 浴用油 / 精油 \"冷杉\"，250毫升",
"price": 114,
"base_price": 346,
"max_discount_price": 79,
"min_seller_price:": 50,
"marketplace_seller_price": 59,
"action_price_to_auto_add": 79,
"min_action_quantity": "0",
"quantity_to_auto_add": "10",
"currency": "RUB"
}
],
"total": "443"
}

复制
收回全部
从促销活动自动添加列表中删除商品
post
/v1/actions/auto-add/products/delete

描述和范例控制台
您可以在讨论的评论中对此方法提供反馈在 Ozon for dev 开发者社区中。

header Parameters
Client-Id
required
string
用户识别号。

Api-Key
required
string
API-密钥。

Request Body schema: application/json
action_id	
integer <uint64>
促销活动标识符。

auto_add_date	
string <date-time>
方法/v1/actions响应中result.auto_add_dates参数里的商品自动添加到促销活动中的日期和时间。

product_ids	
Array of strings <uint64> [ 1 .. 1000 ] items
Ozon系统中的商品标识符，即product_id。

回复
200商品已从自动添加中删除
Response Schema: application/json
product_ids	
Array of strings <uint64>
已从自动添加中删除的商品ID。

400参数有误
403拒绝访问
404未找到答复
409请求冲突
500内部服务器出错
请求范例
Content type
application/json
{
"action_id": "250204",
"auto_add_date": "2035-08-28T14:00:00Z",
"product_ids": [
"14914"
]
}

复制
收回全部
回复范例
200400403404409500
Content type
application/json
{
"product_ids": [
"14914"
]
}

复制
收回全部
在促销活动自动添加列表中添加或更新商品
post
/v1/actions/auto-add/products/update

描述和范例控制台
您可以在讨论的评论中对此方法提供反馈在 Ozon for dev 开发者社区中。

header Parameters
Client-Id
required
string
用户识别号。

Api-Key
required
string
API-密钥。

Request Body schema: application/json
action_id	
integer <uint64>
促销活动标识符。

auto_add_date	
string <date-time>
方法/v1/actions响应中result.auto_add_dates参数里的商品自动添加到促销活动中的日期和时间。

to_update	
Array of objects
需要添加到自动添加中或在自动添加中更新的商品列表。

回复
200商品已在自动添加中添加或更新
Response Schema: application/json
below_min_price	
Array of objects
价格低于最低价格的商品列表。

extremely_low_price	
Array of objects
折扣幅度超过70%的商品列表。

failed_price	
Array of objects
未通过价格校验的商品列表。

rejected	
Array of objects
未能添加或更新的商品ID。

updated_ids	
Array of strings <uint64>
已成功添加或更新的商品ID。

400参数有误
403拒绝访问
404未找到答复
409请求冲突
500内部服务器出错
请求范例
Content type
application/json
{
"action_id": "250204",
"auto_add_dates": "2035-08-28T14:00:00Z",
"to_update": [
{
"currency": "RUB",
"product_id": "14914",
"quantity": 10,
"action_price": 100
}
]
}

复制
收回全部
回复范例
200400403404409500
Content type
application/json
{
"updated_ids": [
"14914"
],
"rejected": [ ],
"below_min_price": [
{
"key": "14914",
"value": 100
}
],
"extremely_low_price": [ ],
"failed_price": [ ]
}

复制
收回全部
使用FBP草稿
获取合作伙伴仓库列表
post
/v1/fbp/warehouse/list

描述和范例控制台
您可以在 讨论 的评论中对此方法提供反馈 在 Ozon for dev 开发者社区中。

header Parameters
Client-Id
required
string
用户识别号。

Api-Key
required
string
API-密钥。

回复
200合作伙伴仓库列表
Response Schema: application/json
warehouses	
Array of objects
仓库列表。

Array ()
address_detailing	
object
地址详情。

id	
integer <int64>
仓库标识符。

is_bonded	
boolean
true，表示该仓库为保税仓。

name	
string
仓库名称。

partner_name	
string
合作伙伴名称。

supply_types	
Array of integers <int32>
交货类型。

timezone_name	
string
仓库所在时区。

default错误
回复范例
200default
Content type
application/json
{
"warehouses": [
{
"address_detailing": {
"city": "string",
"country": "string",
"house": "string",
"region": "string",
"street": "string",
"zipcode": "string"
},
"id": 0,
"is_bonded": true,
"name": "string",
"partner_name": "string",
"supply_types": [
0
],
"timezone_name": "string"
}
]
}

复制
收回全部
获取交货草稿信息
post
/v1/fbp/draft/get

描述和范例控制台
您可以在 讨论 的评论中对此方法提供反馈 在 Ozon for dev 开发者社区中。

Request Body schema: application/json
supply_id
required
string
交货标识符。

回复
200交货草稿详情
Response Schema: application/json
bundle_id	
string
验证后商品的列表标识符。

cancellation_state	
object
取消原因。

created_at	
string <date-time>
草稿创建日期。

decline_reason	
object
拒绝原因。

deleted_at	
string <date-time>
草稿删除日期。

delivery_details	
object
配送详细信息。

editable	
boolean
true，如果草稿可以修改。

id	
integer <int64>
草稿标识符。

is_cancelable	
boolean
true，如果草稿可以取消。

is_deletable	
boolean
true，如果草稿可以删除。

is_registration_available	
boolean
true，如果可注册。

locked	
boolean
true，如果草稿被封锁。

package_units_count	
integer <int32>
货位数量。

row_version	
integer <int64>
草稿的当前版本标识符。

status	
string
Default: "DRAFT_STATUS_UNSPECIFIED"
Enum: "DRAFT_STATUS_UNSPECIFIED" "NEW" "SUPPLY_VARIANT_CONFIRMATION" "SUPPLY_NOT_CONFIRMED"
草稿状态:

DRAFT_STATUS_UNSPECIFIED — 未定义;
NEW — 新的;
SUPPLY_VARIANT_CONFIRMATION — 等待确认;
SUPPLY_NOT_CONFIRMED — 仓库拒收.
supply_id	
string
交货标识符。

warehouse_id	
integer <int64>
仓库标识符。

default错误
请求范例
Content type
application/json
{
"supply_id": "string"
}

复制
收回全部
回复范例
200default
Content type
application/json
{
"bundle_id": "string",
"cancellation_state": {
"cancellation_error": {
"error_code": "CODE_UNSPECIFIED",
"message": "string"
},
"cancellation_status": "STATUS_UNSPECIFIED"
},
"created_at": "2019-08-24T14:15:22Z",
"decline_reason": {
"failed_sku_ids": [
"string"
],
"message": "string"
},
"deleted_at": "2019-08-24T14:15:22Z",
"delivery_details": {
"direct_details": {
"by_seller_details": {
"driver_name": "string",
"vehicle_registration_number": "string",
"vehicle_type": "string"
},
"by_tpl_details": {
"tracking_number": "string",
"transport_company_name": "string"
},
"timeslot_details": {
"timeslot": {
"timeslot_end": "2019-08-24T14:15:22Z",
"timeslot_start": "2019-08-24T14:15:22Z"
},
"timeslot_reservation_id": "string"
}
},
"drop_off_point": {
"id": 0,
"province_uuid": "string",
"timeslot": {
"timeslot_end": "2019-08-24T14:15:22Z",
"timeslot_start": "2019-08-24T14:15:22Z"
}
},
"pickup_details": {
"address": "string",
"comment": "string",
"date": "2019-08-24T14:15:22Z",
"sender_name": "string",
"sender_phone": "string"
},
"supply_type": "SUPPLY_TYPE_UNSPECIFIED"
},
"editable": true,
"id": 0,
"is_cancelable": true,
"is_deletable": true,
"is_registration_available": true,
"locked": true,
"package_units_count": 0,
"row_version": 0,
"status": "DRAFT_STATUS_UNSPECIFIED",
"supply_id": "string",
"warehouse_id": 0
}

复制
收回全部
交货草稿列表
post
/v1/fbp/draft/list

描述和范例控制台
您可以在 讨论 的评论中对此方法提供反馈 在 Ozon for dev 开发者社区中。

Request Body schema: application/json
count
required
integer <int32>
响应中的商品数量。

last_id	
integer <int64>
页面上最后一个值的ID。运行第一个查询时，将此字段留空。

要检索以下数值，请从上一个查询的响应中指定last_id。

回复
200交货草稿列表
Response Schema: application/json
has_next	
boolean
true，如果响应中没有返回所有值。

items	
Array of objects
草稿。

last_id	
integer <int64>
页面上最后一个值的标识符。

default错误
请求范例
Content type
application/json
{
"count": 0,
"last_id": 0
}

复制
收回全部
回复范例
200default
Content type
application/json
{
"has_next": true,
"items": [
{
"bundle_id": "string",
"cancellation_state": {
"cancellation_error": {
"error_code": "CODE_UNSPECIFIED",
"message": "string"
},
"cancellation_status": "STATUS_UNSPECIFIED"
},
"created_at": "2019-08-24T14:15:22Z",
"deleted_at": "2019-08-24T14:15:22Z",
"delivery_details": {
"direct_details": {
"by_seller_details": {
"driver_name": "string",
"vehicle_registration_number": "string",
"vehicle_type": "string"
},
"by_tpl_details": {
"tracking_number": "string",
"transport_company_name": "string"
},
"timeslot_details": {
"timeslot": {
"timeslot_end": "2019-08-24T14:15:22Z",
"timeslot_start": "2019-08-24T14:15:22Z"
},
"timeslot_reservation_id": "string"
}
},
"drop_off_point": {
"id": 0,
"province_uuid": "string",
"timeslot": {
"timeslot_end": "2019-08-24T14:15:22Z",
"timeslot_start": "2019-08-24T14:15:22Z"
}
},
"pickup_details": {
"address": "string",
"comment": "string",
"date": "2019-08-24T14:15:22Z",
"sender_name": "string",
"sender_phone": "string"
},
"supply_type": "SUPPLY_TYPE_UNSPECIFIED"
},
"editable": true,
"id": 0,
"is_cancelable": true,
"is_deletable": true,
"locked": true,
"package_units_count": 0,
"status": "DRAFT_STATUS_UNSPECIFIED",
"supply_id": "string",
"warehouse_id": 0
}
],
"last_id": 0
}

复制
收回全部
处理 FBP direct 交货草稿
创建由卖家配送的草稿
post
/v1/fbp/draft/direct/seller-dlv/create

描述和范例控制台
您可以在 讨论 的评论中对此方法提供反馈 在 Ozon for dev 开发者社区中。

Request Body schema: application/json
bundle_id
required
string
已验证商品清单的标识符。

delivery_details
required
object
配送详情。

package_units_count
required
integer <int32>
货位数量。

warehouse_id
required
integer <int64>
卖家仓库标识符。

回复
200草稿已创建
Response Schema: application/json
draft_id	
integer <int64>
草稿标识符。

row_version	
integer <int64>
草稿的当前版本标识符。

supply_id	
string
供货申请标识符。

default错误
请求范例
Content type
application/json
{
"bundle_id": "string",
"delivery_details": {
"driver_name": "string",
"timeslot_start": "2019-08-24T14:15:22Z",
"vehicle_number": "string",
"vehicle_type": "string"
},
"package_units_count": 0,
"warehouse_id": 0
}

复制
收回全部
回复范例
200default
Content type
application/json
{
"draft_id": 0,
"row_version": 0,
"supply_id": "string"
}

复制
收回全部
更新草稿中由卖家配送的信息
post
/v1/fbp/draft/direct/seller-dlv/edit

描述和范例控制台
您可以在 讨论 的评论中对此方法提供反馈 在 Ozon for dev 开发者社区中。

Request Body schema: application/json
driver_name
required
string
司机姓名。

row_version
required
integer <int64>
草稿的当前版本标识符。

supply_id
required
string
供货申请标识符。

vehicle_number
required
string
车牌号。

vehicle_type
required
string
车辆类型。

回复
200草稿已更新
Response Schema: application/json
error	
object
错误信息。

is_error	
boolean
true，前提是有错误。

row_version	
integer <int64>
草稿的当前版本标识符。

default错误
请求范例
Content type
application/json
{
"driver_name": "string",
"row_version": 0,
"supply_id": "string",
"vehicle_number": "string",
"vehicle_type": "string"
}

复制
收回全部
回复范例
200default
Content type
application/json
{
"error": {
"errors": "ERROR_TYPE_UNSPECIFIED"
},
"is_error": true,
"row_version": 0
}

复制
收回全部
编辑草稿中的时间段
post
/v1/fbp/draft/direct/timeslot/edit

描述和范例控制台
您可以在 讨论 的评论中对此方法提供反馈 在 Ozon for dev 开发者社区中。

Request Body schema: application/json
row_version
required
integer <int64>
草稿的当前版本标识符。

supply_id
required
string
供货申请标识符。

timeslot_start
required
string <date-time>
时间段开始时间。

回复
200时间段已编辑
Response Schema: application/json
error_reasons	
Array of strings
Default: "RESERVE_FAILURE_TYPE_UNSPECIFIED"
Items Enum: "RESERVE_FAILURE_TYPE_UNSPECIFIED" "REQUEST_VALIDATION" "INVALID_RESERVE" "LOGISTICS_REASON" "SCHEDULE_REASON" "NO_CAPACITY"
错误原因：

RESERVE_FAILURE_TYPE_UNSPECIFIED——未定义；
REQUEST_VALIDATION——请求中填写了过去的预定日期；
INVALID_RESERVE——原始预留未找到、已失效或已包含申请，但尝试覆盖；
LOGISTICS_REASON——物流方错误；
SCHEDULE_REASON——排期方错误；
NO_CAPACITY——无可用预定时段。
row_version	
integer <int64>
草稿的当前版本标识符。

default错误
请求范例
Content type
application/json
{
"row_version": 0,
"supply_id": "string",
"timeslot_start": "2019-08-24T14:15:22Z"
}

复制
收回全部
回复范例
200default
Content type
application/json
{
"error_reasons": "RESERVE_FAILURE_TYPE_UNSPECIFIED",
"row_version": 0
}

复制
收回全部
获取直供的时间段列表
post
/v1/fbp/draft/direct/timeslot/get

描述和范例控制台
您可以在 讨论 的评论中对此方法提供反馈 在 Ozon for dev 开发者社区中。

Request Body schema: application/json
bundle_id
required
string
已验证商品清单的标识符。

interval_end
required
string <date-time>
可用时间段所需区间的结束日期。

interval_start
required
string <date-time>
可用时间段所需区间的开始日期。

warehouse_id
required
integer <int64>
卖家仓库标识符。

回复
200时间段列表
Response Schema: application/json
reasons	
Array of strings
Default: "EMPTY_TIMESLOTS_REASON_UNSPECIFIED"
Items Enum: "EMPTY_TIMESLOTS_REASON_UNSPECIFIED" "LOGISTICS_UNKNOWN" "NO_ROUTE" "NO_ROUTE_SCHEDULES" "NO_LOGISTICS_CAPACITY" "SCHEDULE_UNKNOWN" "NOT_ENOUGH_CAPACITY" "NOT_ENOUGH_TRUCKS" "LIMITS_NOT_AVAILABLE" "CROSS_DOCK_RESERVE_MISSING" "SCHEDULE_RESERVE_MISSING"
缺少时间段的原因：

EMPTY_TIMESLOTS_REASON_UNSPECIFIED——未定义；
LOGISTICS_UNKNOWN——物流方未知错误；
NO_ROUTE——没有路线；
NO_ROUTE_SCHEDULES——路线上没有排期；
NO_LOGISTICS_CAPACITY——路线上可用的时段不足；
SCHEDULE_UNKNOWN——排期方未知错误；
NOT_ENOUGH_CAPACITY——仓库可用时段不足；
NOT_ENOUGH_TRUCKS——车辆车位不足；
LIMITS_NOT_AVAILABLE——仓库未设置限制；
CROSS_DOCK_RESERVE_MISSING——仓库未预留越库配送容量；
SCHEDULE_RESERVE_MISSING——缺少必要的排期预留。
timeslots	
Array of objects
可用时间段列表。

warehouse_timezone_name	
string
卖家仓库的时区。

default错误
请求范例
Content type
application/json
{
"bundle_id": "string",
"interval_end": "2019-08-24T14:15:22Z",
"interval_start": "2019-08-24T14:15:22Z",
"warehouse_id": 0
}

复制
收回全部
回复范例
200default
Content type
application/json
{
"reasons": "EMPTY_TIMESLOTS_REASON_UNSPECIFIED",
"timeslots": [
{
"timeslot_end": "2019-08-24T14:15:22Z",
"timeslot_start": "2019-08-24T14:15:22Z"
}
],
"warehouse_timezone_name": "string"
}

复制
收回全部
创建不指定配送方法的交货申请草稿
post
/v1/fbp/draft/direct/create

描述和范例控制台
您可以在 讨论 的评论中对此方法提供反馈 在 Ozon for dev 开发者社区中。

header Parameters
Client-Id
required
string
用户识别号。

Api-Key
required
string
API-密钥。

Request Body schema: application/json
bundle_id
required
string
已校验商品列表的标识符。要获取，请使用方法/v1/fbp/draft/direct/product/validate。

delivery_details
required
object
配送详细信息。

package_units_count
required
integer <int32>
包装单位数量。

warehouse_id
required
integer <int64>
仓库标识符。

回复
200草稿已创建
Response Schema: application/json
draft_id	
integer <int64>
草稿标识符。

row_version	
integer <int64>
草稿的当前版本标识符。

supply_id	
string
交货标识符。

default错误
请求范例
Content type
application/json
{
"bundle_id": "string",
"delivery_details": {
"timeslot_start": "2019-08-24T14:15:22Z"
},
"package_units_count": 0,
"warehouse_id": 0
}

复制
收回全部
回复范例
200default
Content type
application/json
{
"draft_id": 0,
"row_version": 0,
"supply_id": "string"
}

复制
收回全部
删除交货申请草稿
post
/v1/fbp/draft/direct/delete

描述和范例控制台
您可以在 讨论 的评论中对此方法提供反馈 在 Ozon for dev 开发者社区中。

header Parameters
Client-Id
required
string
用户识别号。

Api-Key
required
string
API-密钥。

Request Body schema: application/json
supply_id
required
string
交货标识符。

回复
200草稿已删除
Response Schema: application/json
cancellation_state	
object
取消原因。

row_version	
integer <int64>
草稿的当前版本标识符。

default错误
请求范例
Content type
application/json
{
"supply_id": "string"
}

复制
收回全部
回复范例
200default
Content type
application/json
{
"cancellation_state": {
"cancellation_error": {
"error_code": "CODE_UNSPECIFIED",
"message": "string"
},
"cancellation_status": "STATUS_UNSPECIFIED"
},
"row_version": 0
}

复制
收回全部
检查合作伙伴仓库商品列表
post
/v1/fbp/draft/direct/product/validate

描述和范例控制台
您可以在 讨论 的评论中对此方法提供反馈 在 Ozon for dev 开发者社区中。

header Parameters
Client-Id
required
string
用户识别号。

Api-Key
required
string
API-密钥。

Request Body schema: application/json
skus
required
Array of objects
商品标识符（SKU）列表。

warehouse_id
required
integer <int64>
仓库标识符。

回复
200校验信息
Response Schema: application/json
approved_items	
Array of objects
已确认商品。

bundle_generated	
boolean
true，前提是已创建校验商品列表。

bundle_id	
string
校验商品列表标识符。

rejected_items	
Array of objects
被拒绝的商品。

default错误
请求范例
Content type
application/json
{
"skus": [
{
"count": 0,
"sku": 0
}
],
"warehouse_id": 0
}

复制
收回全部
回复范例
200default
Content type
application/json
{
"approved_items": [
{
"barcode": "string",
"icon_name": "string",
"name": "string",
"offer_id": "string",
"quantity": 0,
"sku": 0,
"volume": 0
}
],
"bundle_generated": true,
"bundle_id": "string",
"rejected_items": [
{
"barcode": "string",
"icon_name": "string",
"name": "string",
"offer_id": "string",
"quantity": 0,
"rejection_reasons": "BUNDLE_ITEM_ERROR_UNSPECIFIED",
"sku": 0,
"volume": 0
}
]
}

复制
收回全部
将草稿单转为正式交货
post
/v1/fbp/draft/direct/registrate

描述和范例控制台
您可以在 讨论 的评论中对此方法提供反馈 在 Ozon for dev 开发者社区中。

header Parameters
Client-Id
required
string
用户识别号。

Api-Key
required
string
API-密钥。

Request Body schema: application/json
row_version
required
integer <int64>
草稿的当前版本标识符。

supply_id
required
string
交货标识符。

回复
200成功
Response Schema: application/json
error	
object
错误。

is_error	
boolean
true，前提是有错误。

row_version	
integer <int64>
草稿的当前版本标识符。

default错误
请求范例
Content type
application/json
{
"row_version": 0,
"supply_id": "string"
}

复制
收回全部
回复范例
200default
Content type
application/json
{
"error": {
"bundle_errors": [
{
"errors": "BUNDLE_ITEM_ERROR_UNSPECIFIED",
"sku": 0
}
],
"order_error": "ORDER_ERROR_TYPE_UNSPECIFIED"
},
"is_error": true,
"row_version": 0
}

复制
收回全部
创建第三方物流公司配送的申请草稿
post
/v1/fbp/draft/direct/tpl-dlv/create

描述和范例控制台
您可以在 讨论 的评论中对此方法提供反馈 在 Ozon for dev 开发者社区中。

Request Body schema: application/json
bundle_id
required
string
套装标识符。

delivery_details
required
object
配送详细信息。

package_units_count
required
integer <int32>
货位数量。

warehouse_id
required
integer <int64>
仓库标识符。

回复
200生成状态
Response Schema: application/json
draft_id	
integer <int64>
草稿标识符。

row_version	
integer <int64>
草稿的当前版本标识符。

supply_id	
string
交货标识符。

default错误
请求范例
Content type
application/json
{
"bundle_id": "string",
"delivery_details": {
"timeslot_start": "2019-08-24T14:15:22Z",
"tracking_number": "string",
"transport_company_name": "string"
},
"package_units_count": 0,
"warehouse_id": 0
}

复制
收回全部
回复范例
200default
Content type
application/json
{
"draft_id": 0,
"row_version": 0,
"supply_id": "string"
}

复制
收回全部
编辑采用第三方承运商配送方法的交货草稿
post
/v1/fbp/draft/direct/tpl-dlv/edit

描述和范例控制台
您可以在 讨论 的评论中对此方法提供反馈 在 Ozon for dev 开发者社区中。

Request Body schema: application/json
row_version
required
integer <int64>
草稿的当前版本标识符。

supply_id
required
string
交货标识符。

tracking_number
required
string
货件跟踪号码。

transport_company_name
required
string
物流公司名称。

回复
200草稿已更改
Response Schema: application/json
error	
object
错误信息。

is_error	
boolean
true，前提是有错误。

row_version	
integer <int64>
草稿的当前版本标识符。

default错误
请求范例
Content type
application/json
{
"row_version": 0,
"supply_id": "string",
"tracking_number": "string",
"transport_company_name": "string"
}

复制
收回全部
回复范例
200default
Content type
application/json
{
"errors": [
"ERROR_TYPE_UNSPECIFIED"
],
"is_error": "true",
"row_version": 0
}

复制
收回全部
处理 FBP drop-off 交货草稿
获取省份列表
post
/v1/fbp/draft/drop-off/province/list

描述和范例控制台
您可以在 讨论 的评论中对此方法提供反馈 在 Ozon for dev 开发者社区中。

header Parameters
Client-Id
required
string
用户识别号。

Api-Key
required
string
API-密钥。

Request Body schema: application/json
warehouse_id
required
integer <int64>
仓库标识符。

回复
200省份列表
Response Schema: application/json
provinces	
Array of objects
省份列表。

Array ()
name	
string
省份名称。

points_count	
integer <int32>
地图上接收点数量。

province_uuid	
string
省份唯一标识符。

default错误
请求范例
Content type
application/json
{
"warehouse_id": 0
}

复制
收回全部
回复范例
200default
Content type
application/json
{
"provinces": [
{
"name": "string",
"points_count": 0,
"province_uuid": "string"
}
]
}

复制
收回全部
获取省份内接收点列表
post
/v1/fbp/draft/drop-off/point/list

描述和范例控制台
您可以在 讨论 的评论中对此方法提供反馈 在 Ozon for dev 开发者社区中。

header Parameters
Client-Id
required
string
用户识别号。

Api-Key
required
string
API-密钥。

Request Body schema: application/json
next_page_number	
integer <int32>
下一页页码。

page_size
required
integer <int32>
每页包含的商品数量。

province_uuid
required
string
省份唯一标识符。

warehouse_id
required
integer <int64>
仓库标识符。

回复
200接收点列表
Response Schema: application/json
drop_off_points	
Array of objects
接收点列表。

Array ()
city	
string
城市。

drop_off_point_id	
integer <int64>
揽收点标识符。

nearest_drop_off_date	
string <date-time>
最近的发运日期。

point_address	
string
接收点地址。

province_uuid	
string
省份唯一标识符。

default错误
请求范例
Content type
application/json
{
"next_page_number": 0,
"page_size": 0,
"province_uuid": "string",
"warehouse_id": 0
}

复制
收回全部
回复范例
200default
Content type
application/json
{
"drop_off_points": [
{
"city": "string",
"drop_off_point_id": 0,
"nearest_drop_off_date": "2019-08-24T14:15:22Z",
"point_address": "string",
"province_uuid": "string"
}
]
}

复制
收回全部
获取接收点的营业时间表
post
/v1/fbp/draft/drop-off/point/timetable

描述和范例控制台
您可以在 讨论 的评论中对此方法提供反馈 在 Ozon for dev 开发者社区中。

header Parameters
Client-Id
required
string
用户识别号。

Api-Key
required
string
API-密钥。

Request Body schema: application/json
drop_off_point_id
required
integer <int64>
揽收点标识符。

province_uuid
required
string
省份唯一标识符。

warehouse_id
required
integer <int64>
仓库标识符。

回复
200营业时间表
Response Schema: application/json
calendar	
Array of objects
接收点的营业时间表。

Array ()
calendar_item	
object
营业时间表。

day_of_week	
string
Default: "DAY_OF_WEEK_UNSPECIFIED"
Enum: "DAY_OF_WEEK_UNSPECIFIED" "MONDAY" "TUESDAY" "WEDNESDAY" "THURSDAY" "FRIDAY" "SATURDAY" "SUNDAY"
星期：

DAY_OF_WEEK_UNSPECIFIED——未指定；
MONDAY——星期一；
TUESDAY——星期二；
WEDNESDAY——星期三；
THURSDAY——星期四；
FRIDAY——星期五；
SATURDAY——星期六；
SUNDAY——星期日。
default错误
请求范例
Content type
application/json
{
"drop_off_point_id": 0,
"province_uuid": "string",
"warehouse_id": 0
}

复制
收回全部
回复范例
200default
Content type
application/json
{
"calendar": [
{
"calendar_item": {
"break_hours": {
"timeslot_end": "string",
"timeslot_start": "string"
},
"is_holiday": true,
"opening_hours": {
"timeslot_end": "string",
"timeslot_start": "string"
}
},
"day_of_week": "DAY_OF_WEEK_UNSPECIFIED"
}
]
}

复制
收回全部
检查合作伙伴仓库可接收的商品列表
post
/v1/fbp/draft/drop-off/product/validate

描述和范例控制台
您可以在 讨论 的评论中对此方法提供反馈 在 Ozon for dev 开发者社区中。

header Parameters
Client-Id
required
string
用户识别号。

Api-Key
required
string
API-密钥。

Request Body schema: application/json
skus
required
Array of objects
Ozon系统中的商品标识符—— SKU。

warehouse_id
required
integer <int64>
仓库标识符。

回复
200检查结果
Response Schema: application/json
approved_items	
Array of objects
已接收的商品。

bundle_generated	
boolean
true，前提是已创建商品成分信息。

bundle_id	
string
验证后的商品列表标识符。

rejected_items	
Array of objects
被拒绝的商品。

default错误
请求范例
Content type
application/json
{
"skus": [
{
"count": 0,
"sku": 0
}
],
"warehouse_id": 0
}

复制
收回全部
回复范例
200default
Content type
application/json
{
"approved_items": [
{
"barcode": "string",
"icon_name": "string",
"name": "string",
"offer_id": "string",
"quantity": 0,
"sku": 0,
"volume": 0
}
],
"bundle_generated": true,
"bundle_id": "string",
"rejected_items": [
{
"barcode": "string",
"icon_name": "string",
"name": "string",
"offer_id": "string",
"quantity": 0,
"rejection_reasons": [
"BUNDLE_ITEM_ERROR_UNSPECIFIED"
],
"sku": 0,
"volume": 0
}
]
}

复制
收回全部
创建接收点配送草稿
post
/v1/fbp/draft/drop-off/create

描述和范例控制台
您可以在 讨论 的评论中对此方法提供反馈 在 Ozon for dev 开发者社区中。

header Parameters
Client-Id
required
string
用户识别号。

Api-Key
required
string
API-密钥。

Request Body schema: application/json
bundle_id
required
string
验证后的商品列表标识符。

delivery_details
required
object
配送详情。

package_units_count
required
integer <int32>
货位数量。

warehouse_id
required
integer <int64>
卖家仓库标识符。

回复
200草稿已创建
Response Schema: application/json
draft_id	
integer <int64>
草稿标识符。

row_version	
integer <int64>
草稿的当前版本标识符。

supply_id	
string
交货申请标识符。

default错误
请求范例
Content type
application/json
{
"bundle_id": "string",
"delivery_details": {
"drop_off_date": "string",
"drop_off_point_id": 0,
"drop_off_province_uuid": "string"
},
"package_units_count": 0,
"warehouse_id": 0
}

复制
收回全部
回复范例
200default
Content type
application/json
{
"draft_id": 0,
"row_version": 0,
"supply_id": "string"
}

复制
收回全部
删除接收点配送草稿
post
/v1/fbp/draft/drop-off/delete

描述和范例控制台
您可以在 讨论 的评论中对此方法提供反馈 在 Ozon for dev 开发者社区中。

header Parameters
Client-Id
required
string
用户识别号。

Api-Key
required
string
API-密钥。

Request Body schema: application/json
supply_id
required
string
交货申请标识符。

回复
200草稿已删除
Response Schema: application/json
cancellation_state	
object
取消原因。

row_version	
integer <int64>
草稿的当前版本标识符。

default错误
请求范例
Content type
application/json
{
"supply_id": "string"
}

复制
收回全部
回复范例
200default
Content type
application/json
{
"cancellation_state": {
"cancellation_error": {
"error_code": "NO_RESPONSE_FROM_3PF",
"message": "string"
},
"cancellation_status": "CONFIRMATION"
},
"row_version": 0
}

复制
收回全部
编辑接收点配送草稿的配送详情
post
/v1/fbp/draft/drop-off/dlv/edit

描述和范例控制台
您可以在 讨论 的评论中对此方法提供反馈 在 Ozon for dev 开发者社区中。

header Parameters
Client-Id
required
string
用户识别号。

Api-Key
required
string
API-密钥。

Request Body schema: application/json
drop_off_date
required
string
送货日期。

drop_off_point_id
required
integer <int64>
揽收点标识符。

drop_off_province_uuid
required
string
省份唯一标识符。

row_version
required
integer <int64>
草稿的当前版本标识符。

supply_id
required
string
交货申请标识符。

回复
200成功
Response Schema: application/json
row_version	
integer <int64>
草稿的当前版本标识符。

default错误
请求范例
Content type
application/json
{
"drop_off_date": "string",
"drop_off_point_id": 0,
"drop_off_province_uuid": "string",
"row_version": 0,
"supply_id": "string"
}

复制
收回全部
回复范例
200default
Content type
application/json
{
"row_version": 0
}

复制
收回全部
将草稿转为正式交货
post
/v1/fbp/draft/drop-off/registrate

描述和范例控制台
您可以在 讨论 的评论中对此方法提供反馈 在 Ozon for dev 开发者社区中。

header Parameters
Client-Id
required
string
用户识别号。

Api-Key
required
string
API-密钥。

Request Body schema: application/json
row_version
required
integer <int64>
草稿的当前版本标识符。

supply_id
required
string
交货申请标识符。

回复
200成功
Response Schema: application/json
error	
object
错误。

is_error	
boolean
true，前提是有错误。

row_version	
integer <int64>
草稿的当前版本标识符。

default错误
请求范例
Content type
application/json
{
"row_version": 0,
"supply_id": "string"
}

复制
收回全部
回复范例
200default
Content type
application/json
{
"error": {
"bundle_errors": [
{
"errors": [
"BUNDLE_ITEM_ERROR_UNSPECIFIED"
],
"sku": 0
}
],
"order_error": "ORDER_ERROR_TYPE_UNSPECIFIED"
},
"is_error": true,
"row_version": 0
}

复制
收回全部
处理 FBP pick-up 交货草稿
创建 pick-up 交货申请草稿
post
/v1/fbp/draft/pick-up/create

描述和范例控制台
您可以在讨论的评论中对此方法提供反馈在 Ozon for dev 开发者社区中。

Request Body schema: application/json
bundle_id
required
string
已校验商品列表的标识符。

delivery_details
required
object
配送详细信息。

package_units_count
required
integer <int32>
包装单位数量。

warehouse_id
required
integer <int64>
仓库标识符。

回复
200草稿已创建
Response Schema: application/json
draft_id	
integer <int64>
草稿标识符。

row_version	
integer <int64>
草稿的当前版本标识符。

supply_id	
string
交货标识符。

default错误
请求范例
Content type
application/json
{
"bundle_id": "string",
"delivery_details": {
"address": "string",
"comment": "string",
"date": "2019-08-24T14:15:22Z",
"sender_name": "string",
"sender_phone": "string"
},
"package_units_count": 0,
"warehouse_id": 0
}

复制
收回全部
回复范例
200default
Content type
application/json
{
"draft_id": 0,
"row_version": 0,
"supply_id": "string"
}

复制
收回全部
取消 pick-up 交货申请草稿
post
/v1/fbp/draft/pick-up/delete

描述和范例控制台
您可以在讨论的评论中对此方法提供反馈在 Ozon for dev 开发者社区中。

Request Body schema: application/json
supply_id
required
string
交货标识符。

回复
200草稿已取消
Response Schema: application/json
cancellation_state	
object
取消原因。

row_version	
integer <int64>
草稿的当前版本标识符。

default错误
请求范例
Content type
application/json
{
"supply_id": "string"
}

复制
收回全部
回复范例
200default
Content type
application/json
{
"cancellation_state": {
"cancellation_error": {
"error_code": "CODE_UNSPECIFIED",
"message": "string"
},
"cancellation_status": "STATUS_UNSPECIFIED"
},
"row_version": 0
}

复制
收回全部
修改 pick-up 交货申请
post
/v1/fbp/draft/pick-up/dlv/edit

描述和范例控制台
您可以在讨论的评论中对此方法提供反馈在 Ozon for dev 开发者社区中。

Request Body schema: application/json
pickup_details
required
object
Детали доставки.

row_version
required
integer <int64>
草稿的当前版本标识符。

supply_id
required
string
交货标识符。

回复
200信息已编辑
Response Schema: application/json
row_version	
integer <int64>
草稿的当前版本标识符。

default错误
请求范例
Content type
application/json
{
"pickup_details": {
"address": "string",
"comment": "string",
"date": "2019-08-24T14:15:22Z",
"sender_name": "string",
"sender_phone": "string"
},
"row_version": 0,
"supply_id": "string"
}

复制
收回全部
回复范例
200default
Content type
application/json
{
"row_version": 0
}

复制
收回全部
验证用于 pick-up 交货的商品列表
post
/v1/fbp/draft/pick-up/product/validate

描述和范例控制台
您可以在讨论的评论中对此方法提供反馈在 Ozon for dev 开发者社区中。

Request Body schema: application/json
skus
required
Array of objects
商品标识符（SKU）列表。

warehouse_id
required
integer <int64>
仓库标识符。

回复
200列表已验证
Response Schema: application/json
approved_items	
Array of objects
已确认商品。

bundle_generated	
boolean
true，前提是已创建校验商品列表。

bundle_id	
string
校验商品列表标识符。

rejected_items	
Array of objects
被拒绝的商品。

default错误
请求范例
Content type
application/json
{
"skus": [
{
"count": 0,
"sku": 0
}
],
"warehouse_id": 0
}

复制
收回全部
回复范例
200default
Content type
application/json
{
"approved_items": [
{
"barcode": "string",
"icon_name": "string",
"name": "string",
"offer_id": "string",
"quantity": 0,
"sku": 0,
"volume": 0
}
],
"bundle_generated": true,
"bundle_id": "string",
"rejected_items": [
{
"barcode": "string",
"icon_name": "string",
"name": "string",
"offer_id": "string",
"quantity": 0,
"rejection_reasons": [
"BUNDLE_ITEM_ERROR_UNSPECIFIED"
],
"sku": 0,
"volume": 0
}
]
}

复制
收回全部
将草稿单转为正式交货
post
/v1/fbp/draft/pick-up/registrate

描述和范例控制台
您可以在 讨论 的评论中对此方法提供反馈 在 Ozon for dev 开发者社区中。

header Parameters
Client-Id
required
string
用户识别号。

Api-Key
required
string
API-密钥。

Request Body schema: application/json
row_version
required
integer <int64>
草稿的当前版本标识符。

supply_id
required
string
交货申请标识符。

回复
200成功
Response Schema: application/json
error	
object
错误。

is_error	
boolean
true，前提是有错误。

row_version	
integer <int64>
草稿的当前版本标识符。

default错误
请求范例
Content type
application/json
{
"row_version": 0,
"supply_id": "string"
}

复制
收回全部
回复范例
200default
Content type
application/json
{
"error": {
"bundle_errors": [
{
"errors": [
"BUNDLE_ITEM_ERROR_UNSPECIFIED"
],
"sku": 0
}
],
"order_error": "ORDER_ERROR_TYPE_UNSPECIFIED"
},
"is_error": true,
"row_version": 0
}

复制
收回全部
处理 FBP direct 请求
取消交货
post
/v1/fbp/order/direct/cancel

描述和范例控制台
您可以在 讨论 的评论中对此方法提供反馈 在 Ozon for dev 开发者社区中。

Request Body schema: application/json
supply_id
required
string
供货申请标识符。

回复
200取消结果
Response Schema: application/json
error	
object
错误信息。

is_error	
boolean
true，前提是有错误。

row_version	
integer <int64>
草稿的当前版本标识符。

default错误
请求范例
Content type
application/json
{
"supply_id": "string"
}

复制
收回全部
回复范例
200default
Content type
application/json
{
"example": {
"error": {
"order_errors": [
"ERROR_TYPE_UNSPECIFIED"
]
},
"is_error": true,
"row_version": 0
}
}

复制
收回全部
更新卖家自配送信息
post
/v1/fbp/order/direct/seller-dlv/edit

描述和范例控制台
您可以在 讨论 的评论中对此方法提供反馈 在 Ozon for dev 开发者社区中。

Request Body schema: application/json
driver_name
required
string
司机姓名。

row_version
required
integer <int64>
草稿的当前版本标识符。

supply_id
required
string
供货申请标识符。

vehicle_number
required
string
车牌号。

vehicle_type
required
string
车辆类型。

回复
200信息已更新
Response Schema: application/json
error	
object
错误信息。

is_error	
boolean
true，前提是有错误。

row_version	
integer <int64>
草稿的当前版本标识符。

default错误
请求范例
Content type
application/json
{
"driver_name": "string",
"row_version": 0,
"supply_id": "string",
"vehicle_number": "string",
"vehicle_type": "string"
}

复制
收回全部
回复范例
200default
Content type
application/json
{
"error": {
"order_errors": "ERROR_TYPE_UNSPECIFIED"
},
"is_error": true,
"row_version": 0
}

复制
收回全部
编辑交货申请中的时间段
post
/v1/fbp/order/direct/timeslot/edit

描述和范例控制台
您可以在 讨论 的评论中对此方法提供反馈 在 Ozon for dev 开发者社区中。

Request Body schema: application/json
row_version
required
integer <int64>
草稿的当前版本标识符。

supply_id
required
string
供货申请标识符。

timeslot_start
required
string <date-time>
时间段开始时间。

回复
200时间段已编辑
Response Schema: application/json
error_reasons	
Array of strings
Items Enum: "RESERVE_FAILURE_TYPE_UNSPECIFIED" "REQUEST_VALIDATION" "INVALID_RESERVE" "LOGISTICS_REASON" "SCHEDULE_REASON"
错误原因：

RESERVE_FAILURE_TYPE_UNSPECIFIED——未定义；
REQUEST_VALIDATION——请求中填写了过去的预定日期；
INVALID_RESERVE——原始预留未找到、已失效或已包含申请，但尝试覆盖；
LOGISTICS_REASON——物流方错误；
SCHEDULE_REASON——排期方错误；
NO_CAPACITY——无可用预定时段。
row_version	
integer <int64>
草稿的当前版本标识符。

default错误
请求范例
Content type
application/json
{
"row_version": 0,
"supply_id": "string",
"timeslot_start": "2019-08-24T14:15:22Z"
}

复制
收回全部
回复范例
200default
Content type
application/json
{
"error_reasons": [
"RESERVE_FAILURE_TYPE_UNSPECIFIED"
],
"row_version": 0
}

复制
收回全部
获取交货时间段列表
post
/v1/fbp/order/direct/timeslot/list

描述和范例控制台
您可以在 讨论 的评论中对此方法提供反馈 在 Ozon for dev 开发者社区中。

Request Body schema: application/json
interval_end
required
string <date-time>
可用时间段所需区间的结束日期。

interval_start
required
string <date-time>
可用时间段所需区间的开始日期。

supply_id
required
string
交货标识符。

回复
200时间段列表
Response Schema: application/json
reasons	
Array of strings
Items Enum: "EMPTY_TIMESLOTS_REASON_UNSPECIFIED" "LOGISTICS_UNKNOWN" "NO_ROUTE" "NO_ROUTE_SCHEDULES" "NO_LOGISTICS_CAPACITY" "SCHEDULE_UNKNOWN" "NOT_ENOUGH_CAPACITY" "NOT_ENOUGH_TRUCKS" "LIMITS_NOT_AVAILABLE" "CROSS_DOCK_RESERVE_MISSING" "SCHEDULE_RESERVE_MISSING"
缺少时间段的原因：

EMPTY_TIMESLOTS_REASON_UNSPECIFIED——未定义；
LOGISTICS_UNKNOWN——物流方未知错误；
NO_ROUTE——没有路线；
NO_ROUTE_SCHEDULES——路线上没有排期；
NO_LOGISTICS_CAPACITY——路线上可用的时段不足；
SCHEDULE_UNKNOWN——排期方未知错误；
NOT_ENOUGH_CAPACITY——仓库可用时段不足；
NOT_ENOUGH_TRUCKS——车辆车位不足；
LIMITS_NOT_AVAILABLE——仓库未设置限制；
CROSS_DOCK_RESERVE_MISSING——仓库未预留越库配送容量；
SCHEDULE_RESERVE_MISSING——缺少必要的排期预留。
timeslots	
Array of objects
可用时间段列表。

warehouse_timezone_name	
string
卖家仓库的时区。

default错误
请求范例
Content type
application/json
{
"interval_end": "2019-08-24T14:15:22Z",
"interval_start": "2019-08-24T14:15:22Z",
"supply_id": "string"
}

复制
收回全部
回复范例
200default
Content type
application/json
{
"reasons": [
"EMPTY_TIMESLOTS_REASON_UNSPECIFIED"
],
"timeslots": [
{
"timeslot_end": "2019-08-24T14:15:22Z",
"timeslot_start": "2019-08-24T14:15:22Z"
}
],
"warehouse_timezone_name": "string"
}

复制
收回全部
处理 FBP drop-off 请求
取消 drop-off 交货
post
/v1/fbp/order/drop-off/cancel

描述和范例控制台
您可以在 讨论 的评论中对此方法提供反馈 在 Ozon for dev 开发者社区中。

header Parameters
Client-Id
required
string
用户识别号。

Api-Key
required
string
API-密钥。

Request Body schema: application/json
supply_id
required
string
交货标识符。

回复
200交货已取消
Response Schema: application/json
error	
object
错误信息。

is_error	
boolean
true，前提是有错误。

row_version	
integer <int64>
草稿的当前版本标识符。

default错误
请求范例
Content type
application/json
{
"supply_id": "string"
}

复制
收回全部
回复范例
200default
Content type
application/json
{
"error": {
"order_errors": [
"ERROR_TYPE_UNSPECIFIED"
]
},
"is_error": true,
"row_version": 0
}

复制
收回全部
编辑收货点的送货信息
post
/v1/fbp/order/drop-off/dlv/edit

描述和范例控制台
您可以在 讨论 的评论中对此方法提供反馈 在 Ozon for dev 开发者社区中。

header Parameters
Client-Id
required
string
用户识别号。

Api-Key
required
string
API-密钥。

Request Body schema: application/json
drop_off_date
required
string
交货到揽收点的到达日期。

row_version
required
integer <int64>
草稿的当前版本标识符。

supply_id
required
string
交货标识符。

回复
200信息已传递
Response Schema: application/json
row_version	
integer <int64>
草稿的当前版本标识符。

default错误
请求范例
Content type
application/json
{
"drop_off_date": "string",
"row_version": 0,
"supply_id": "string"
}

复制
收回全部
回复范例
200default
Content type
application/json
{
"row_version": 0
}

复制
收回全部
获取接收点的营业时间表
post
/v1/fbp/order/drop-off/timetable

描述和范例控制台
您可以在 讨论 的评论中对此方法提供反馈 在 Ozon for dev 开发者社区中。

header Parameters
Client-Id
required
string
用户识别号。

Api-Key
required
string
API-密钥。

Request Body schema: application/json
drop_off_point_id
required
integer <int64>
揽收点标识符。

province_uuid
required
string
省份唯一标识符。

warehouse_id
required
integer <int64>
仓库标识符。

回复
200营业时间表已获取
Response Schema: application/json
calendar	
Array of objects
接收点的营业时间信息。

Array ()
calendar_item	
object
日期信息。

day_of_week	
string
Default: "DAY_OF_WEEK_UNSPECIFIED"
Enum: "DAY_OF_WEEK_UNSPECIFIED" "MONDAY" "TUESDAY" "WEDNESDAY" "THURSDAY" "FRIDAY" "SATURDAY" "SUNDAY"
星期：

DAY_OF_WEEK_UNSPECIFIED——未指定；
MONDAY——星期一；
TUESDAY——星期二；
WEDNESDAY——星期三；
THURSDAY——星期四；
FRIDAY——星期五；
SATURDAY——星期六；
SUNDAY——星期日。
default错误
请求范例
Content type
application/json
{
"drop_off_point_id": 0,
"province_uuid": "string",
"warehouse_id": 0
}

复制
收回全部
回复范例
200default
Content type
application/json
{
"calendar": [
{
"calendar_item": {
"break_hours": {
"timeslot_end": "string",
"timeslot_start": "string"
},
"is_holiday": true,
"opening_hours": {
"timeslot_end": "string",
"timeslot_start": "string"
}
},
"day_of_week": "DAY_OF_WEEK_UNSPECIFIED"
}
]
}

复制
收回全部
处理 FBP pick-up 请求
取消上门揽收交货
post
/v1/fbp/order/pick-up/cancel

描述和范例控制台
您可以在 讨论 的评论中对此方法提供反馈 在 Ozon for dev 开发者社区中。

Request Body schema: application/json
supply_id
required
string
交货标识符。

回复
200取消状态
Response Schema: application/json
error	
object
错误信息。

is_error	
boolean
true，前提是有错误。

row_version	
integer <int64>
草稿的当前版本标识符。

default错误
请求范例
Content type
application/json
{
"supply_id": "string"
}

复制
收回全部
回复范例
200default
Content type
application/json
{
"error": {
"order_errors": "ERROR_TYPE_UNSPECIFIED"
},
"is_error": true,
"row_version": 0
}

复制
收回全部
更改取货地点信息
post
/v1/fbp/order/pick-up/dlv/edit

描述和范例控制台
您可以在 讨论 的评论中对此方法提供反馈 在 Ozon for dev 开发者社区中。

Request Body schema: application/json
pickup_details
required
object
发件人详细信息。

row_version
required
integer <int64>
草稿的当前版本标识符。

supply_id
required
string
交货标识符。

回复
200更改状态
Response Schema: application/json
error	
object
错误信息。

is_error	
boolean
true，前提是有错误。

row_version	
integer <int64>
草稿的当前版本标识符。

default错误
请求范例
Content type
application/json
{
"pickup_details": {
"sender_name": "string",
"sender_phone": "string"
},
"row_version": 0,
"supply_id": "string"
}

复制
收回全部
回复范例
200default
Content type
application/json
{
"error": {
"order_errors": "ERROR_TYPE_UNSPECIFIED"
},
"is_error": true,
"row_version": 0
}

复制
收回全部
FBP配送
生成验收证明书
post
/v1/fbp/act-from/create

描述和范例控制台
您可以在 讨论 的评论中对此方法提供反馈 在 Ozon for dev 开发者社区中。

Request Body schema: application/json
supply_id
required
string
交货标识符。

回复
200成功
Response Schema: application/json
errors	
Array of strings
Default: "CREATE_ACT_ERROR_REASON_UNSPECIFIED"
Items Enum: "CREATE_ACT_ERROR_REASON_UNSPECIFIED" "INVALID_ORDER_TYPE"
错误原因：

CREATE_ACT_ERROR_REASON_UNSPECIFIED ——未定义；
INVALID_ORDER_TYPE ——无法为指定标识符创建验收证明书。
file_uuid	
string
验收证明书标识符。

is_success	
boolean
true，前提是请求中没有错误。

default错误
请求范例
Content type
application/json
{
"supply_id": "string"
}

复制
收回全部
回复范例
200default
Content type
application/json
{
"errors": [
"CREATE_ACT_ERROR_REASON_UNSPECIFIED"
],
"file_uuid": "string",
"is_success": true
}

复制
收回全部
获取验收证明书生成状态
post
/v1/fbp/act-from/get

描述和范例控制台
您可以在 讨论 的评论中对此方法提供反馈 在 Ozon for dev 开发者社区中。

Request Body schema: application/json
file_uuid
required
string
验收证明书标识符。

回复
200验收证明书生成状态
Response Schema: application/json
cdn_url	
string
验收证明书链接。

error	
string
Default: "ERROR_REASON_UNSPECIFIED"
Enum: "ERROR_REASON_UNSPECIFIED" "INVALID_COMPANY" "FILE_NOT_FOUND" "GENERATE_TIMEOUT_REACHED" "GENERATION_ERROR"
生成错误：

ERROR_REASON_UNSPECIFIED ——未定义；
INVALID_COMPANY ——公司无效；
FILE_NOT_FOUND ——文件未找到；
GENERATE_TIMEOUT_REACHED ——超出生成时间；
GENERATION_ERROR ——生成过程中出错。
status	
string
Default: "STATUS_UNSPECIFIED"
Enum: "STATUS_UNSPECIFIED" "NOT_EXIST" "PROCESSING" "EXIST" "ERROR"
生成状态：

STATUS_UNSPECIFIED ——未定义；
NOT_EXIST ——不存在；
PROCESSING ——处理中；
EXIST ——已完成；
ERROR ——错误。
default错误
请求范例
Content type
application/json
{
"file_uuid": "string"
}

复制
收回全部
回复范例
200default
Content type
application/json
{
"cdn_url": "string",
"error": "ERROR_REASON_UNSPECIFIED",
"status": "STATUS_UNSPECIFIED"
}

复制
收回全部
生成货物运单
post
/v1/fbp/act-to/create

描述和范例控制台
您可以在 讨论 的评论中对此方法提供反馈 在 Ozon for dev 开发者社区中。

Request Body schema: application/json
supply_id
required
string
交货标识符。

回复
200成功
Response Schema: application/json
code	
string
货物运单标识符。

default错误
请求范例
Content type
application/json
{
"supply_id": "string"
}

复制
收回全部
回复范例
200default
Content type
application/json
{
"code": "string"
}

复制
收回全部
获取货物运单生成状态
post
/v1/fbp/act-to/get

描述和范例控制台
您可以在 讨论 的评论中对此方法提供反馈 在 Ozon for dev 开发者社区中。

Request Body schema: application/json
code
required
string
货物运单标识符。

supply_id
required
string
交货标识符。

回复
200货物运单生成状态
Response Schema: application/json
error_message	
string
错误描述。

label_url	
string
交货标签链接。

state	
string
Default: "STATE_TYPE_UNSPECIFIED"
Enum: "STATE_TYPE_UNSPECIFIED" "IN_PROGRESS" "FINISHED" "FAILED"
生成状态：

STATE_TYPE_UNSPECIFIED ——未定义；
IN_PROGRESS ——进行中；
FINISHED ——成功完成；
FAILED ——错误。
default错误
请求范例
Content type
application/json
{
"code": "string",
"supply_id": "string"
}

复制
收回全部
回复范例
200default
Content type
application/json
{
"error_message": "string",
"label_url": "string",
"state": "STATE_TYPE_UNSPECIFIED"
}

复制
收回全部
获取已完成交货信息
post
/v1/fbp/archive/get

描述和范例控制台
您可以在讨论的评论中对此方法提供反馈在 Ozon for dev 开发者社区中。

Request Body schema: application/json
supply_id
required
string
交货标识符。

回复
200已完成交货信息
Response Schema: application/json
act_file_uuid	
string
验收证明书标识符。

bundle_id	
string
已验证商品清单的标识符。

bundle_sku_summary	
object
交货商品汇总信息。

business_flow_type_id	
integer <int64>
交货类型标识符。

created_date	
string <date-time>
交货申请创建日期和时间。

decline_reason	
object
拒绝交货的原因。

delivery_details	
object
配送详情。

has_act	
boolean
true，前提是已生成交接单。

has_label	
boolean
true，前提是已生成标签。

id	
integer <int64>
档案记录编号。

order_draft_id	
integer <int64>
交货草稿标识符。

order_number	
string
已完成交货标识符。

package_units_count	
integer <int32>
货位数量。

receive_date	
string <date-time>
交货接收日期和时间。

row_version	
integer <int64>
草稿的当前版本标识符。

status	
string
Default: "ARCHIVE_STATUS_UNSPECIFIED"
Enum: "ARCHIVE_STATUS_UNSPECIFIED" "COMPLETED" "REJECTED_AT_SUPPLY_WAREHOUSE" "CANCELLED_BY_SELLER"
已完成的交货状态：

ARCHIVE_STATUS_UNSPECIFIED：未指定；
COMPLETED：已完成；
REJECTED_AT_SUPPLY_WAREHOUSE：被仓库拒绝；
CANCELLED_BY_SELLER：卖家取消。
supply_id	
string
交货标识符。

warehouse_id	
integer <int64>
仓库标识符。

default错误
请求范例
Content type
application/json
{
"supply_id": "string"
}

复制
收回全部
回复范例
200default
Content type
application/json
{
"act_file_uuid": "string",
"bundle_id": "string",
"bundle_sku_summary": {
"rounded_total_volume_in_litres": 0,
"total_items_count": 0,
"total_quantity": 0
},
"business_flow_type_id": 0,
"created_date": "2019-08-24T14:15:22Z",
"decline_reason": {
"code": "DECLINE_REASON_CODE_UNSPECIFIED",
"message": "string"
},
"delivery_details": {
"direct_details": {
"by_seller_details": {
"driver_name": "string",
"vehicle_registration_number": "string",
"vehicle_type": "string"
},
"by_tpl_details": {
"tracking_number": "string",
"transport_company_name": "string"
},
"timeslot_details": {
"timeslot": {
"timeslot_end": "2019-08-24T14:15:22Z",
"timeslot_start": "2019-08-24T14:15:22Z"
},
"timeslot_reservation_id": "string"
}
},
"drop_off_point": {
"id": 0,
"province_uuid": "string",
"timeslot": {
"timeslot_end": "2019-08-24T14:15:22Z",
"timeslot_start": "2019-08-24T14:15:22Z"
}
},
"pickup_details": {
"address": "string",
"comment": "string",
"date": "2019-08-24T14:15:22Z",
"sender_name": "string",
"sender_phone": "string"
},
"supply_type": "SUPPLY_TYPE_UNSPECIFIED"
},
"has_act": true,
"has_label": true,
"id": 0,
"order_draft_id": 0,
"order_number": "string",
"package_units_count": 0,
"receive_date": "2019-08-24T14:15:22Z",
"row_version": 0,
"status": "ARCHIVE_STATUS_UNSPECIFIED",
"supply_id": "string",
"warehouse_id": 0
}

复制
收回全部
获取已完成交货列表
post
/v1/fbp/archive/list

描述和范例控制台
您可以在讨论的评论中对此方法提供反馈在 Ozon for dev 开发者社区中。

Request Body schema: application/json
count
required
string <int32>
响应中的元素数量。

last_id	
string <int64>
页面上最后一个值的标识符。首次请求时请留空。

如需获取后续数据，请填写上次响应中的 last_id。

回复
200已完成交货列表
Response Schema: application/json
has_next	
boolean
true，前提是本次响应未返回所有数据。

items	
Array of objects
已完成交货。

last_id	
integer <int64>
页面上最后一个值的标识符。

default错误
请求范例
Content type
application/json
{
"count": "string",
"last_id": "string"
}

复制
收回全部
回复范例
200default
Content type
application/json
{
"has_next": true,
"items": [
{
"act_file_uuid": "string",
"bundle_id": "string",
"bundle_sku_summary": {
"rounded_total_volume_in_litres": 0,
"total_items_count": 0,
"total_quantity": 0
},
"created_date": "2019-08-24T14:15:22Z",
"decline_reason": {
"code": "DECLINE_REASON_CODE_UNSPECIFIED",
"message": "string"
},
"delivery_details": {
"direct_details": {
"by_seller_details": {
"driver_name": "string",
"vehicle_registration_number": "string",
"vehicle_type": "string"
},
"by_tpl_details": {
"tracking_number": "string",
"transport_company_name": "string"
},
"timeslot_details": {
"timeslot": {
"timeslot_end": "2019-08-24T14:15:22Z",
"timeslot_start": "2019-08-24T14:15:22Z"
},
"timeslot_reservation_id": "string"
}
},
"drop_off_point": {
"id": 0,
"province_uuid": "string",
"timeslot": {
"timeslot_end": "2019-08-24T14:15:22Z",
"timeslot_start": "2019-08-24T14:15:22Z"
}
},
"pickup_details": {
"address": "string",
"comment": "string",
"date": "2019-08-24T14:15:22Z",
"sender_name": "string",
"sender_phone": "string"
},
"supply_type": "SUPPLY_TYPE_UNSPECIFIED"
},
"external_order_id": "string",
"has_act": true,
"has_label": true,
"order_draft_id": 0,
"package_units_count": 0,
"receive_date": "2019-08-24T14:15:22Z",
"row_version": 0,
"status": "ARCHIVE_STATUS_UNSPECIFIED",
"supply_id": "string",
"warehouse_id": 0,
"whc_order_id": 0
}
],
"last_id": 0
}

复制
收回全部
创建标签生成任务
post
/v1/fbp/label/create

描述和范例控制台
您可以在讨论的评论中对此方法提供反馈在 Ozon for dev 开发者社区中。

Request Body schema: application/json
supply_id
required
string
交货标识符。

回复
200任务已创建
Response Schema: application/json
code	
string
标签生成任务标识符。

default错误
请求范例
Content type
application/json
{
"supply_id": "string"
}

复制
收回全部
回复范例
200default
Content type
application/json
{
"code": "string"
}

复制
收回全部
获取标签生成任务状态
post
/v1/fbp/label/get

描述和范例控制台
您可以在讨论的评论中对此方法提供反馈在 Ozon for dev 开发者社区中。

Request Body schema: application/json
code
required
string
标签生成任务标识符。

supply_id
required
string
交货标识符。

回复
200任务已创建
Response Schema: application/json
label_url	
string
交货标签链接。

state	
string
Default: "UNSPECIFIED"
Enum: "UNSPECIFIED" "IN_PROGRESS" "FINISHED" "FAILED"
标签生成任务状态：

UNSPECIFIED：未指定；
IN_PROGRESS：生成中；
FINISHED：生成成功；
FAILED：生成失败。
default错误
请求范例
Content type
application/json
{
"code": "string",
"supply_id": "string"
}

复制
收回全部
回复范例
200default
Content type
application/json
{
"label_url": "string",
"state": "UNSPECIFIED"
}

复制
收回全部
获取关于特定交货的信息
post
/v1/fbp/order/get

描述和范例控制台
您可以在 讨论 的评论中对此方法提供反馈 在 Ozon for dev 开发者社区中。

Request Body schema: application/json
supply_id
required
string
交货标识符。

回复
200配送详情
Response Schema: application/json
attention_reasons	
Array of strings
Default: "ORDER_ATTENTION_TYPE_UNSPECIFIED"
Items Enum: "ORDER_ATTENTION_TYPE_UNSPECIFIED" "OLD" "TIME_SLOT_EXPIRED"
警告原因：

ORDER_ATTENTION_TYPE_UNSPECIFIED——未指定；
OLD——过期申请；
TIME_SLOT_EXPIRED——时间段已过期。
bundle_uuid	
string
组成商品标识符。

can_be_cancelled	
boolean
true，如果申请可以取消。

cancellation_state	
object
取消原因。

created_date	
string <date-time>
交货创建日期。

delivery_details	
object
配送详情。

draft_id	
integer <int64>
草稿标识符。

has_consignment_note	
boolean
true，如果有已签署的文件。

has_label	
boolean
true，如果有标签。

id	
integer <int64>
交货申请标识符。

locked	
boolean
true，如果无法编辑交货。

order_number	
string
交货编号。

package_units_count	
integer <int32>
货位数量。

receive_date	
string <date-time>
交货接收日期和时间。

row_version	
integer <int64>
草稿的当前版本标识符。

status	
string
Default: "ORDER_STATUS_UNSPECIFIED"
Enum: "ORDER_STATUS_UNSPECIFIED" "READY_TO_SUPPLY" "FILLING_DELIVERY_DETAILS" "COURIER_ASSIGNED" "COURIER_PICKED_UP" "ACCEPTANCE_AT_DROP_OFF_POINT" "IN_TRANSIT_TO_STORAGE_WAREHOUSE" "ACCEPTANCE_AT_STORAGE_WAREHOUSE" "CANCELLED"
订单状态：

ORDER_STATUS_UNSPECIFIED——未指定；
READY_TO_SUPPLY——准备发运；
FILLING_DELIVERY_DETAILS——填写交货数据；
COURIER_ASSIGNED——已分配快递员；
COURIER_PICKED_UP——快递员已取件；
ACCEPTANCE_AT_DROP_OFF_POINT——已在揽收点接收；
IN_TRANSIT_TO_STORAGE_WAREHOUSE——在运往存储仓库的途中；
ACCEPTANCE_AT_STORAGE_WAREHOUSE——仓库验收中；
CANCELLED——申请已取消。
supply_id	
string
交货申请标识符。

warehouse_id	
integer <int64>
仓库标识符。

default错误
请求范例
Content type
application/json
{
"supply_id": "string"
}

复制
收回全部
回复范例
200default
Content type
application/json
{
"attention_reasons": "ORDER_ATTENTION_TYPE_UNSPECIFIED",
"bundle_uuid": "string",
"can_be_cancelled": true,
"cancellation_state": {
"cancellation_error": {
"error_code": "CODE_UNSPECIFIED",
"message": "string"
},
"cancellation_status": "STATUS_UNSPECIFIED"
},
"created_date": "2019-08-24T14:15:22Z",
"delivery_details": {
"direct_details": {
"by_seller_details": {
"driver_name": "string",
"vehicle_registration_number": "string",
"vehicle_type": "string"
},
"by_tpl_details": {
"tracking_number": "string",
"transport_company_name": "string"
},
"timeslot_details": {
"timeslot": {
"timeslot_end": "2019-08-24T14:15:22Z",
"timeslot_start": "2019-08-24T14:15:22Z"
},
"timeslot_reservation_id": "string"
}
},
"drop_off_point": {
"id": 0,
"province_uuid": "string",
"timeslot": {
"timeslot_end": "2019-08-24T14:15:22Z",
"timeslot_start": "2019-08-24T14:15:22Z"
}
},
"pickup_details": {
"address": "string",
"comment": "string",
"date": "2019-08-24T14:15:22Z",
"sender_name": "string",
"sender_phone": "string"
},
"supply_type": "SUPPLY_TYPE_UNSPECIFIED"
},
"draft_id": 0,
"has_consignment_note": true,
"has_label": true,
"id": 0,
"locked": true,
"order_number": "string",
"package_units_count": 0,
"receive_date": "2019-08-24T14:15:22Z",
"row_version": 0,
"status": "ORDER_STATUS_UNSPECIFIED",
"supply_id": "string",
"warehouse_id": 0
}

复制
收回全部
获取交货列表
post
/v1/fbp/order/list

描述和范例控制台
您可以在 讨论 的评论中对此方法提供反馈 在 Ozon for dev 开发者社区中。

Request Body schema: application/json
count
required
integer <int32>
响应中的交货数量。

last_id	
integer <int64>
页面上最后一次交货的标识符。首次请求时请将此字段留空。

如需获取后续数据，请填写上一次请求响应中最后一次交货的id。

回复
200送货清单
Response Schema: application/json
has_next	
boolean
true，如果响应中未返回所有交货。

items	
Array of objects
交货。

last_id	
integer <int64>
页面上最后一次交货的标识符。

default错误
请求范例
Content type
application/json
{
"count": 0,
"last_id": 0
}

复制
收回全部
回复范例
200default
Content type
application/json
{
"has_next": true,
"items": [
{
"attention_reasons": "ORDER_ATTENTION_TYPE_UNSPECIFIED",
"bundle_summary": {
"rounded_total_volume_in_litres": 0,
"total_item_count": 0,
"total_quantity": 0
},
"can_be_cancelled": true,
"cancellation_state": {
"cancellation_error": {
"error_code": "CODE_UNSPECIFIED",
"message": "string"
},
"cancellation_status": "STATUS_UNSPECIFIED"
},
"created_date": "2019-08-24T14:15:22Z",
"delivery_details": {
"direct_details": {
"by_seller_details": {
"driver_name": "string",
"vehicle_registration_number": "string",
"vehicle_type": "string"
},
"by_tpl_details": {
"tracking_number": "string",
"transport_company_name": "string"
},
"timeslot_details": {
"timeslot": {
"timeslot_end": "2019-08-24T14:15:22Z",
"timeslot_start": "2019-08-24T14:15:22Z"
},
"timeslot_reservation_id": "string"
}
},
"drop_off_point": {
"id": 0,
"province_uuid": "string",
"timeslot": {
"timeslot_end": "2019-08-24T14:15:22Z",
"timeslot_start": "2019-08-24T14:15:22Z"
}
},
"pickup_details": {
"address": "string",
"comment": "string",
"date": "2019-08-24T14:15:22Z",
"sender_name": "string",
"sender_phone": "string"
},
"supply_type": "SUPPLY_TYPE_UNSPECIFIED"
},
"has_consignment_note": true,
"has_label": true,
"id": 0,
"locked": true,
"order_number": "string",
"package_units_count": 0,
"receive_date": "2019-08-24T14:15:22Z",
"status": "ORDER_STATUS_UNSPECIFIED",
"supply_id": "string",
"warehouse_id": 0
}
],
"last_id": 0
}

复制
收回全部
获取货件列表
post
/v1/posting/fbp/list

描述和范例控制台
您可以在 讨论 的评论中对此方法提供反馈 在 Ozon for dev 开发者社区中。

header Parameters
Client-Id
required
string
用户识别号。

Api-Key
required
string
API-密钥。

Request Body schema: application/json
cursor	
string
用于选择下一批数据的指针。

filter	
object
用于搜索货件的筛选器。

limit	
integer <int64> [ 1 .. 100 ]
响应中返回的值数量。

sort_by	
string
货件排序参数：

last_change_status_date——按最后一次状态变更日期排序；
in_process_at——按开始处理日期排序。
sort_dir	
string
Enum: "ASC" "DESC"
排序方向：

ASC——升序；
DESC——降序。
回复
200货件列表
Response Schema: application/json
cursor	
string
用于选择下一批数据的指针。

postings	
Array of objects
货件列表。

400参数有误
403拒绝访问
404未找到答案
409请求冲突
500内部服务器错误
请求范例
Content type
application/json
{
"cursor": "string",
"filter": {
"name": "string",
"offer_id": "string",
"posting_numbers": [
"string"
],
"since": "2019-08-24T14:15:22Z",
"statuses": [
"string"
],
"to": "2019-08-24T14:15:22Z"
},
"limit": 1,
"sort_by": "string",
"sort_dir": "ASC"
}

复制
收回全部
回复范例
200400403404409500
Content type
application/json
{
"cursor": "string",
"postings": [
{
"financial_data": {
"cluster_from": "string",
"cluster_to": "string",
"delivery_amount": 0,
"products": [
{
"actions": [
{
"action_id": "string",
"date_from": "2019-08-24T14:15:22Z",
"date_to": "2019-08-24T14:15:22Z",
"discount_percent": 0,
"discount_value": 0,
"is_from_seller": true,
"description": "string"
}
],
"commissions_currency_code": "string",
"old_price": 0,
"price": 0,
"product_id": 0,
"quantity": 0,
"total_discount_percent": 0,
"total_discount_value": 0
}
]
},
"in_process_at": "2019-08-24T14:15:22Z",
"order_date": "2019-08-24T14:15:22Z",
"order_id": 0,
"order_number": "string",
"posting_number": "string",
"products": [
{
"customer_price": {
"amount": "string",
"currency": "string"
},
"name": "string",
"offer_id": "string",
"price": {
"amount": "string",
"currency": "string"
},
"quantity": 0,
"seller_price": {
"amount": "string",
"currency": "string"
},
"sku": 0
}
],
"provider_id": 0,
"status": "string"
}
]
}

复制
收回全部
Premium
分析数据
post
/v1/analytics/data

描述和范例控制台
适用于订阅了 Premium Plus 或 Premium Pro 的卖家。

请指定需要计算的时间段和指标。响应将包含按dimensions参数分组的分析。

从一个卖家账号每分钟可以发送1次请求。 与个人中心中的分析→图表部分相符。

header Parameters
Client-Id
required
string
用户识别号。

Api-Key
required
string
API-密钥。

Request Body schema: application/json
date_from
required
string
数据将出现在报告中的日期。

若您没有Premium订阅，请指定过去三个月内的日期。

date_to
required
string
数据将出现在报告中的截止日期。

dimension
required
Array of strings
Items Enum: "unknownDimension" "sku" "spu" "day" "week" "month" "year" "category1" "category2" "brand" "modelID" "descriptionType"
报告中的分组数据。

所有卖家可用的分组方法：

unknownDimension — 未知商品标识符；
sku — 商品标识符；
spu — 商品标识符 — 统一商品卡片；
day — 日；
week — 星期；
month — 月。
只有Premium订阅卖家才能使用的分组方法：

year — 年；
category1 — 一级类别；
category2 — 二级类别；
brand — 品牌；
modelID — 型号；
descriptionType — 商品类型。
filters	
Array of objects
过滤器。

limit
required
integer <int64>
响应的值个数：

最大值 — 1000，
最小值 — 1.
metrics
required
Array of strings
Items Enum: "unknown_metric" "hits_view_search" "hits_view_pdp" "hits_view" "hits_tocart_search" "hits_tocart_pdp" "hits_tocart" "session_view_search" "session_view_pdp" "session_view" "conv_tocart_search" "conv_tocart_pdp" "conv_tocart" "revenue" "returns" "cancellations" "ordered_units" "delivered_units" "adv_view_pdp" "adv_view_search_category" "adv_view_all" "adv_sum_all" "position_category" "postings" "postings_premium"
最多指定14个指标。如有更多，您将收到 InvalidArgument的错误。

生成报告所依据的指标列表。

所有卖家可用的指标：

revenue — 订购的金额，
ordered_units — 订购的商品。
仅对Premium订阅卖家可用的指标：

unknown_metric — 未知指标。
hits_view_search — 在搜索和类别中的指标。
hits_view_pdp — 商品卡片上的指标。
hits_view — 总展示次数。
hits_tocart_search — 从搜索或类别添加到购物车。
hits_tocart_pdp — 从商品卡片添加到购物车。
hits_tocart — 添加到购物车的总数。
session_view_search — 带有在搜索结果或目录中展示的会话。计算在搜索结果或目录中有浏览的唯一身份访问者。
session_view_pdp — 在商品卡片上显示的会话。计算查看过商品卡片的唯一身份访问者。
session_view — 所有会话。计算唯一身份访问者。
conv_tocart_search — 从商品卡片转换到购物车。
conv_tocart_pdp — 从商品卡片转换到购物车的总转化率。
conv_tocart — 购物车总转化率。
returns — 退货。
cancellations — 取消的商品。
delivered_units — 交付的商品。
position_category — 在搜索和类别中的的位置。
offset	
integer <int64>
响应中要跳过的元素数字。例如，如果 offset = 10, 那么答案将从找到的第11个元素开始。

sort	
Array of objects
报告排列设置。

回复
200数据分析
Response Schema: application/json
result	
object
查询结果。

data	
Array of objects
数据组。

totals	
Array of numbers <double>
指标总计和平均值。

timestamp	
string
报告创建时间。

400参数错误
403拒绝访问
404未找到答案
409请求冲突
500内部服务器错误
请求范例
Content type
application/json
{
"date_from": "2020-09-01",
"date_to": "2021-10-15",
"metrics": [
"hits_view_search"
],
"dimension": [
"sku",
"day"
],
"filters": [ ],
"sort": [
{
"key": "hits_view_search",
"order": "DESC"
}
],
"limit": 1000,
"offset": 0
}

复制
收回全部
回复范例
200400403404409500
Content type
application/json
{
"result": {
"data": [ ],
"totals": [
0
]
},
"timestamp": "2021-11-25 15:19:21"
}

复制
收回全部
获取商品搜索查询信息
post
/v1/analytics/product-queries

描述和范例控制台
使用该方法可以获取您的商品在 Ozon 平台上的搜索查询信息。完整分析仅对订阅 Premium、Premium Plus 或 Premium Pro 的用户开放。未订阅的用户可以查看部分指标。该方法类似于个人中心的 搜索中的商品 → 我的商品的查询 选项卡。

您可以按指定日期查看查询分析。为此，需在请求中指定 date_from 和 date_to 参数。最近一个月的数据可按任意区间查看，但不包含当天的数据——相关数据需 1–2 天完成计算后才会更新。一个月之前的数据仅对订阅 Premium、Premium Plus或Premium Pro的用户开放，且仅支持按周查看——在请求中请填写date_from参数。

header Parameters
Client-Id
required
string
用户识别号。

Api-Key
required
string
API-密钥。

Request Body schema: application/json
date_from
required
string <date-time>
分析数据的起始日期。

date_to	
string <date-time>
分析数据的结束日期。

page	
integer <int32> >= 0
请求返回的页码。

page_size
required
integer <int32> <= 1000
每页包含的商品数量。

skus
required
Array of strings <int64>
SKU 列表，即 Ozon 系统中的商品标识符。根据这些 SKU 返回搜索查询的分析数据。最多可查询 1000 个 SKU。

sort_by	
string
Default: "BY_SEARCHES"
Enum: "BY_SEARCHES" "BY_VIEWS" "BY_POSITION" "BY_CONVERSION" "BY_GMV"
按具体参数对商品进行排序。可能的取值：

BY_SEARCHES— 按搜索次数；
BY_VIEWS— 按浏览量；
BY_POSITION— 按商品的平均排名；
BY_CONVERSION— 按转化率；
BY_GMV — 按搜索查询的销售额。
sort_dir	
string
Default: "DESCENDING"
Enum: "DESCENDING" "ASCENDING"
排序方向：

DESCENDING— 降序；
ASCENDING— 升序。
回复
200商品搜索查询信息
Response Schema: application/json
analytics_period	
object
数据分析的时间范围。

items	
Array of objects
商品列表。

page_count	
integer <int64>
总页数。

total	
integer <int64>
搜索请求的总数。

400参数错误
403拒绝访问
404无响应
409请求冲突
500服务器内部错误
请求范例
Content type
application/json
{
"date_from": "2019-08-24T14:15:22Z",
"date_to": "2019-08-24T14:15:22Z",
"page": 0,
"page_size": 1000,
"skus": [
"string"
],
"sort_by": "BY_SEARCHES",
"sort_dir": "DESCENDING"
}

复制
收回全部
回复范例
200400403404409500
Content type
application/json
{
"analytics_period": {
"date_from": "string",
"date_to": "string"
},
"items": [
{
"category": "string",
"currency": "string",
"gmv": 0,
"name": "string",
"offer_id": "string",
"position": 0,
"sku": 0,
"unique_search_users": 0,
"unique_view_users": 0,
"view_conversion": 0
}
],
"page_count": 0,
"total": 0
}

复制
收回全部
有关特定商品查询的信息
post
/v1/analytics/product-queries/details

描述和范例控制台
使用该方法获取特定商品的查询数据。完整分析仅对订阅 Premium、Premium Plus 或 Premium Pro 的用户开放。未订阅的用户可以查看部分指标。该方法与在个人中心的 搜索中的商品 → 我的商品查询 选项卡查看商品数据类似。

您可以按指定日期查看查询分析。为此，需在请求中指定 date_from 和 date_to 参数。最近一个月的数据可按任意区间查看，但不包含当天的数据——相关数据需 1–2 天完成计算后才会更新。一个月之前的数据仅对订阅 Premium、Premium Plus或Premium Pro的用户开放，且仅支持按周查看——在请求中请填写date_from参数。

header Parameters
Client-Id
required
string
用户识别号。

Api-Key
required
string
API-密钥。

Request Body schema: application/json
date_from
required
string <date-time>
分析数据的起始日期。

date_to	
string <date-time>
分析数据的结束日期。

limit_by_sku
required
integer <int32>
单个SKU的查询数量限制。最大值为15次查询。

page	
integer <int32>
请求返回的页码。最小值为0。

page_size
required
integer <int32>
每页包含的商品数量。最大值为100。

skus
required
Array of strings <int64>
SKU 列表，即 Ozon 系统中的商品标识符。根据这些 SKU 返回搜索查询的分析数据。最多可查询 1000 个 SKU。

sort_by	
string
Default: "BY_SEARCHES"
Enum: "BY_SEARCHES" "BY_VIEWS" "BY_POSITION" "BY_CONVERSION" "BY_GMV"
按具体参数对商品进行排序。可能的取值：

BY_SEARCHES— 按搜索次数；
BY_VIEWS— 按浏览量；
BY_POSITION— 按商品的平均排名；
BY_CONVERSION— 按转化率；
BY_GMV — 按搜索查询的销售额。
只有 Premium 或 Premium Plus 订阅，才能按 BY_VIEWS、BY_POSITION 和 BY_CONVERSION 排序。

sort_dir	
string
Default: "DESCENDING"
Enum: "DESCENDING" "ASCENDING"
排序方向：

DESCENDING— 降序；
ASCENDING— 升序。
回复
200有关特定商品查询的信息
Response Schema: application/json
analytics_period	
object
数据分析的时间范围。

page_count	
integer <int64>
总页数。

queries	
Array of objects
查询列表。

total	
integer <int64>
搜索请求的总数。

400参数错误
请求范例
Content type
application/json
{
"date_from": "2019-08-24T14:15:22Z",
"date_to": "2019-08-24T14:15:22Z",
"limit_by_sku": 0,
"page": 0,
"page_size": 1000,
"skus": [
"string"
],
"sort_by": "BY_SEARCHES",
"sort_dir": "DESCENDING"
}

复制
收回全部
回复范例
200400
Content type
application/json
{
"analytics_period": {
"date_from": "string",
"date_to": "string"
},
"page_count": 0,
"queries": [
{
"currency": "string",
"gmv": 0,
"order_count": 0,
"position": 0,
"query": "string",
"query_index": 0,
"sku": 0,
"unique_search_users": 0,
"unique_view_users": 0,
"view_conversion": 0
}
],
"total": 0
}

复制
收回全部
每日商品销售报告
post
/v1/finance/realization/by-day

描述和范例控制台
该方法返回每日商品销售报告中的销售金额数据。不包括取消和无人认领的订单。数据仅可获取从当前日期起最多32个自然日之内的记录。此方法仅对 Premium Plus 或 Premium Pro 订阅的用户开放。

header Parameters
Client-Id
required
string
用户识别号。

Api-Key
required
string
API-密钥。

Request Body schema: application/json
day
required
integer <int32>
日。

month
required
integer <int32>
月。

year
required
integer <int32>
年。

回复
200每日销售报告
Response Schema: application/json
rows	
Array of objects
报告表格。

Array ()
commission_ratio	
number <double>
按类目划分的销售佣金比例。

delivery_commission	
object
配送佣金。

item	
object
商品信息。

return_commission	
object
商品退货佣金。

rowNumber	
integer <int32>
报告中的行号。

seller_price_per_instance	
number <double>
考虑折扣后的卖家价格。

400参数错误
403访问被拒绝
404未找到响应
409请求冲突
500服务器内部错误
请求范例
Content type
application/json
{
"day": 0,
"month": 0,
"year": 0
}

复制
收回全部
回复范例
200400403404409500
Content type
application/json
{
"rows": [
{
"commission_ratio": 0,
"delivery_commission": {
"amount": 0,
"bonus": 0,
"commission": 0,
"compensation": 0,
"price_per_instance": 0,
"quantity": 0,
"standard_fee": 0,
"bank_coinvestment": 0,
"stars": 0,
"total": 0
},
"item": {
"barcode": "string",
"name": "string",
"offer_id": "string",
"sku": 0
},
"return_commission": {
"amount": 0,
"bonus": 0,
"commission": 0,
"compensation": 0,
"price_per_instance": 0,
"quantity": 0,
"standard_fee": 0,
"bank_coinvestment": 0,
"stars": 0,
"total": 0
},
"rowNumber": 0,
"seller_price_per_instance": 0
}
]
}

复制
收回全部
获取按文本筛选的搜索查询列表
post
/v1/search-queries/text

描述和范例控制台
仅对拥有Premium Pro订阅的卖家开放。

header Parameters
Client-Id
required
string
用户识别号。

Api-Key
required
string
API-密钥。

Request Body schema: application/json
limit
required
string <int64> <= 50
每页的结果数量。

offset
required
string <int64> <= 50
响应中将被跳过的项目数量。

sort_by	
string
Enum: "CLIENT_COUNT" "ADD_TO_CART" "CONVERSION_TO_CART" "AVG_PRICE"
排序搜索查询的参数：

CLIENT_COUNT——查询的受欢迎程度；
ADD_TO_CART——添加到购物车的次数；
CONVERSION_TO_CART——购物车转化率；
AVG_PRICE——平均价格。
sort_dir	
string
Enum: "ASC" "DESC"
排序方向：

ASC——升序；
DESC——降序。
text
required
string
按文本搜索。

回复
200搜索查询列表
Response Schema: application/json
offset	
string <int64>
К每页显示的搜索查询数量。

search_queries	
Array of objects
搜索查询信息。

total	
string <int64>
搜索查询总数。

400参数错误
403拒绝访问
404未找到答案
409请求冲突
500内部服务器错误
请求范例
Content type
application/json
{
"limit": "50",
"offset": "0",
"sort_by": "CLIENT_COUNT",
"sort_dir": "ASC",
"text": "string"
}

复制
收回全部
回复范例
200400403404409500
Content type
application/json
{
"search_queries": [
{
"avg_price": 3786.6,
"conversion_to_cart": 0.163,
"client_count": 165418,
"items_views": 140.828,
"query": "куртка женская демисезон",
"add_to_cart": 26977,
"sellers_count": 63.833
},
{
"avg_price": 3786.6,
"conversion_to_cart": 0.163,
"client_count": 165418,
"items_views": 140.828,
"query": "куртка женская демисезон",
"add_to_cart": 26977,
"sellers_count": 63.833
}
],
"offset": "string",
"total": "string"
}

复制
收回全部
获取热门搜索查询列表
post
/v1/search-queries/top

描述和范例控制台
仅对拥有Premium Pro订阅的卖家开放。

header Parameters
Client-Id
required
string
用户识别号。

Api-Key
required
string
API-密钥。

Request Body schema: application/json
limit
required
string <int64> <= 50
每页的结果数量。

offset
required
string <int64> <= 1000
响应中将被跳过的项目数量。

回复
200热门搜索查询列表
Response Schema: application/json
offset	
string <int64>
每页显示的搜索查询数量。

search_queries	
Array of objects
搜索查询信息。

total	
string <int64>
搜索查询总数。

400参数错误
403拒绝访问
404未找到答案
409请求冲突
500内部服务器错误
请求范例
Content type
application/json
{
"limit": "50",
"offset": "0"
}

复制
收回全部
回复范例
200400403404409500
Content type
application/json
{
"search_queries": [
{
"avg_price": 3786.6,
"conversion_to_cart": 0.163,
"client_count": 165418,
"items_views": 140.828,
"query": "куртка женская демисезон",
"add_to_cart": 26977,
"sellers_count": 63.833
}
],
"offset": "1",
"total": "1"
}

复制
收回全部
获取商品价格的详细信息
post
/v1/product/prices/details

描述和范例控制台
仅对 Premium Pro 订阅的卖家开放。

header Parameters
Client-Id
required
string
用户识别号。

Api-Key
required
string
API-密钥。

Request Body schema: application/json
skus
required
Array of strings <int64> [ 1 .. 1000 ] items
SKU列表。

回复
200商品价格信息
Response Schema: application/json
prices	
Array of objects
商品价格。

Array ()
customer_price	
object
网站上的商品价格。

discount_percent	
number <float>
Deprecated
由 Ozon 承担的折扣比例。

offer_id	
string
卖家系统中的商品标识符（商品货号）。

price	
object
商品价格（已包含促销活动或推广优惠）。

price_indexes	
Array of objects
价格指数。

sku	
integer <int64>
Ozon 系统中的商品标识符——SKU。

400参数错误
403访问被拒绝
404响应未找到
409请求冲突
500服务器内部错误
请求范例
Content type
application/json
{
"skus": [
"string"
]
}

复制
收回全部
回复范例
200400403404409500
Content type
application/json
{
"prices": [
{
"customer_price": {
"amount": "string",
"currency": "string"
},
"discount_percent": 0,
"offer_id": "string",
"price": {
"amount": "string",
"currency": "string"
},
"price_indexes": [
{
"external_index_data": {
"min_price": {
"amount": "string",
"currency": "string"
},
"price_index": 0,
"url": "string"
},
"self_index_data": {
"min_price": {
"amount": "string",
"currency": "string"
},
"price_index": 0,
"url": "string"
}
}
],
"sku": 0
}
]
}

复制
收回全部
错误
所有方法
错误文本	错误描述
Circle is open	如果正在执行大量查询，那么系统将封锁该工作方法。在几分钟后，该方法会正常运作。
Internal error	服务器还没来得及处理请求。
Invalid Api-Key, please check the key and try again	无效的 API 密钥：请检查密钥并重试。
Api-key is deactivated, use another one or generate a new one	API 密钥已被禁用：请使用其他密钥或生成新的密钥。
Api-Key is missing a required role for a method	API 密钥缺少执行该方法所需的权限。
Api-Key is restricted to specific IP addresses	API 密钥仅允许特定 IP 地址访问。
You have reached request rate limit per second	您已超出请求限制：同一Client ID每秒最多允许50次请求。还需同时遵循各个具体方法的限制。
error limiting: acquire limit per item: items limit: limit exceeded	您对商品价格的更新过于频繁。商品价格每1小时仅可更新10次。
method is not allowed	方法不存在。请检查请求类型并重试。
/v5/fbs/posting/product/exemplar/validate
错误	描述
GTD_MUST_BE_SPECIFIED_FOR_PRODUCT_COUNTRY	未指出货物海关申报号。如果没有货物海关申报号，请转达“is_gtd_absent: true”。
/v2/products/stocks
错误文本	错误描述
product_is_not_created	商品尚未通过审核，因此暂时无法更新库存。请等待price_sent状态后再尝试。
offer_id_not_found	您的个人中心中找不到该SKU的商品。
FLAMMABLE_ONLY_ON_SELF_OR_PROVIDER_DELIVERY	易燃商品只能从您的仓库销售，并由您自行或第三方服务配送。请选择其他仓库或新建一个仓库后再试。
在卖家知识库中了解更多关于危险商品销售的信息
WAREHOUSE_NOT_FOUND	ID为warehouse_id的仓库未找到。请检查仓库ID是否有错，并确保其状态为活跃状态。
PRODUCT_HAS_NOT_BEEN_TAGGED_YET	商品尚未被标记为 “КГТ“ (超大货件 — Bulky product) 或者 “неКГТ“ (非超大货件 — Non-bulky product), 因为未指定商品尺寸或标记系统尚未处理。
NON_KGT_ON_KGT_WAREHOUSE	尝试在大件商品仓库中设置或者更新非大件商品库存。
MP_DELIVERY_ONLY_3PL_ERROR	商品不能放置于使用Ozon物流的仓库中。
TOO_MANY_REQUESTS	您过于频繁地更新同一商品库存。对于同货号商品，同一个货号的库存每30秒只能更新一次。
Stock is updated too frequently	您尝试对同一商品的库存进行过于频繁的更新。同一仓库的商品库存每30秒只能更新1次。请确保您的集成程序未在后台模式下更新库存。
MULTIBOX_NOT_ALLOWED_FOR_FBS	在FBS模式下，不可以将来自多个箱子的商品整合成一个商品。请从stock字段中删除值并重试。
OVER_MAX_OVH_NON_KGT	无法从您选择的仓库销售超大商品。请选择另一个仓库或创建一个新的仓库并重试。
OVER_MAX_OVH_KGT	商品重量或尺寸超过了所选仓库的最大值。请修改商品特性或选择另一个仓库。
SOURCE_TYPE_NOT_FOUND	商品没有SKU。请检查商品是否已创建并正确设置。
Request validation error: invalid ProductsStocksRequest.Stocks[0]: embedded message failed validation	请求中未指定仓库ID。可通过/v1/warehouse/list方法来查询。
STOCK_TOO_BIG	您为商品库存指定的值过大。请将数量设定为小于一百万并重新尝试。
NOT_FOUND_ERROR	商品未能在个人中心找到。
SIZE_REQUIRED_FOR_NOT_UNIQUE_OFFER_ID	该商品的商品编码与其他商品的编码重复。对于普通商品，请设置参数 quant_size = 1，对于定量包装，请设置 quant_size = 2 或更大。
CB_DELIVERY_ONLY_FBP	该商品仅可通过 FBP 仓库进行销售。
/v4/posting/fbs/ship
错误文本	错误描述
TRANSITION_IS_NOT_POSSIBLE	您输入了错误的 rFBS 订单状态顺序。请使用 /v3/posting/fbs/get 方法获取当前货件状态。状态更改是异步进行的。
HAS_INCORRECT_TPL_INTEGRATION_TYPE	尝试在集成交付服务时将状态转成rFBS订单。
POSTING_NOT_FOUND	订单不在合作伙伴的个人中心中。
POSTING_ALREADY_CANCELLED	订单已取消。
POSTING_ALREADY_SHIPPED	订单已收集。
HAS_INCORRECT_STATUS	订单状态不正确。
HAS_INCORRECT_PRODUCT_QUANTITY	请求中的商品数量或SKU错误。
UNKNOW_PRODUCT/UNKNOWN_PRODUCT_DEFINED	商品ID指定有误。请检查您是否在product_id字段中输入了商品的SKU。
EXEMPLAR_INFO_ALREADY_DEFINED	商品样品信息已经更新，无需再次传输数据。
MANDATORY_MARK_REDUNDANT	无需为商品赋值标签代码。
EXEMPLAR_INFO_NOT_FILLED_COMPLETELY	请确保您已经完整传达了订单中每个商品实例的所有信息。
/v2/posting/fbs/package-label
错误文本	错误描述
The next postings aren't ready	这些商品还没有准备好贴标签。
INVALID_ARGUMENT	请求体中传递了错误的数值。只有状态为“等待发货”的订单才能打印标签"——awaiting_deliver。请确认货件的是正确的状态，并且传递了正确的数据。
NO_POSTINGS_FOR_BATCH_DOWNLOAD	请求中没有处于“等待发运”状态下的订单。
label not allowed for delivered postings	请求中包含状态并非“等待发运”——awaiting_deliver的货件。
/v1/product/import/prices
错误文本	错误描述
invalid_category_price	尝试设定过高或者过低的商品价。
discount_for_average_price_is_too_small	折扣太少。如果打折后的价格在400-10000卢布（含）之间，打折前后的价格差必须在5%以上。
discount_for_low_price_is_too_small	折扣太少。 如果折扣后的价格低于400卢布（含），则折扣前后的价格差必须在20卢布以上。
discount_too_big	折扣太多。 折扣前后价格差必须小于90%。
discount_for_top_price_is_too_small	折扣太小。如果折后价格高于10000卢布，则打折前后的价格差必须在500卢布以上。
price_negative	尝试设置负价格。
NOT_FOUND_ERROR	在个人中心中没有含此ID的商品。
/v3/product/import
错误文本	错误描述
SPU_already_exists	含有此特征的商品已存在。
"Invalid_state" - Product is not ready to supply	商品尚不具备库存更新的条件。可能是商品未创建或账户未激活。
Incorrect_density	商品未通过密度检查。您提供的密度超出了允许范围。密度的最小值为0.001，最大值为13.55。

密度按以下公式计算：重量 × 1000 ÷ (高度 × 宽度 × 深度)。

同时，请确保您使用的商品质量和体积值是正确的。
price_is_negative	未指定 price 参数。
SELLER_NO_CONTRACT_FAILED	合同已到期。如需上传商品，请在个人中心续签合同。
error_attribute_values_empty	未填写商品特征。请填写attributes.values参数。
error_attribute_values_out_of_range	商品特征值填写有误。
missing_dimension	未填写商品尺寸和重量。请填写items.height、items.width、items.depth和items.weight参数。
在卖家知识库中了解更多关于体积重量特征的信息
VALUE_MAX_LIMIT	已超出商品尺寸限制。
在卖家知识库中了解更多关于体积重量特征的信息
EMPTY_REQUIRED	未填写必填字段。
description_category_invalid	类目未找到或未填写。请使用/v1/description-category/tree方法检查类目。
description_category_has_no_description_type	商品类型与指定类目不匹配。请使用/v1/description-category/tree方法获取类目和类型列表。请使用所选类目最后一级的值。
有关类目和商品类型的更多信息，请参阅卖家知识库
description_category_is_legacy и levels_category_not_found	类目已过时。请使用/v1/description-category/tree方法获取当前类目列表。
有关商品类目的更多信息，请参阅卖家知识库
description_category_is_empty	未填写类目——items.description_category_id。
description_type_is_empty	未填写商品类型——items.type_id。
有关商品类型的更多信息，请参阅卖家知识库
vat_invalid	增值税填写有误。
name_too_long	商品名称过长。最大长度为255个字符。
有关商品标题的更多信息，请参阅卖家知识库
all_image_failed	通过链接下载图片失败。请检查链接无误、可公开访问且无需授权。
有关商品图片要求的更多信息
invalid_rich_content_json	JSON格式的富内容与模板不匹配。请在沙箱中检查代码。
有关富内容的更多信息
all_image_unprocessed	照片上传失败。请检查链接无误、可公开访问且无需授权。
有关商品图片要求的更多信息
price_out_of_range	价格不得低于该类目的最低门槛或高于最高门槛。请设置市场均价。

如果您需要将价格设定在某个类目的门槛之上或以下，请写信给客服：商品和价格 → 价格管理 → 设置价格时出错。
old_price_less_than_price	原价必须高于当前售价。请使用/v1/product/import/pricess方法更新items.old_price参数的值。
min_auto_price_too_big	自动应用折扣和促销活动后的价格必须低于您的售价。请使用/v1/product/import/prices方法更新min_price参数的值。
min_auto_price_too_small	最低价格不得低于您售价的50%。请使用/v1/product/import/prices方法更新min_price参数的值。
price_less_than_min_auto_price	您的售价必须高于最低价格。请使用/v1/product/import/prices方法更新price和min_price参数的值。
/v2/review/list
错误文本	错误描述
not available with existing subscription	方法不可用。请开通方法描述中指定的订阅。
在卖家知识库中详细了解订阅
/v1/product/import/info
错误文本	错误描述
result: items: 0	请确保标明正确的商品类别，并已输入增值税。
v2/posting/fbs/cancel
错误文本	错误描述
HAS_INCORRECT_CANCEL_REASON	所提供的订单取消ID有误。
/v6/fbs/posting/product/exemplar/set, /v5/fbs/posting/product/exemplar/status
错误文本	错误描述
GTD_IS_REQUIRED_ONLY_FOR_LEGAL_CUSTOMER	货物报关单只能提交给法人。
EXEMPLAR_ID does not belong to product PRODUCT_ID	实例标识符 exemplar_id 与商品标识符product_id 不匹配。请使用/v6/fbs/posting/product/exemplar/create-or-get 方法获取正确的exemplar_id。
/v1/product/unarchive
错误文本	错误描述
restore limit exceeded	您已超出自动归档商品卡片的恢复次数上限。每天最多只能恢复100个商品卡片。限额会在莫斯科时间03:00重置。
total limit exceeded	您已超出在个人中心的商品数量上限。请编辑活跃卡片或者将其中一部分移至归档。可以通过方法 /v4/product/info/limit 查询商品数量上限。
简介
本栏目介绍了如何开启推送通知，从而从Ozon接收事件信息到您的服务器中。

新发件的创建。
发件取消。
发件状态的变化。
发件快递或装运日期变化。
您也可以收到由于您的服务器无法使用而没有被送达的消息和通知的信息。

如何连接


您的服务器应该根据REST API标准和文档中规定的错误代码来发送回应。
如果你的服务器的回应偏离了所需的结构，通知发送可能会被暂停。

发送通知的IP地址

195.34.21.0/24,
185.73.192.0/22,
91.223.93.0/24.

要接收通知，请发送请求至 sapi-push@ozon.ru。 在申请中请注明:

您的 seller_id。
向其发送通知的服务器URL-地址。 比如, https://www.example.com/api/method。
您想收到的通知清单。
三个工作日内您的服务器将被绑定。

连接推送通知时的错误代码
错误	描述	解决方法
REQUEST_ERROR	请求未发送，无法连接到指定地址。	请检查您的服务是否正常运行。
REQUEST_TIMEOUT	请求超时。	增加请求的等待时间。
SERVER_FAULT	您的服务返回了服务器内部错误。	请检查服务器日志，更新服务器软件，增加分配的资源或联系服务器管理员。
STATUS_CODE_NOT_OK	服务的HTTP响应状态码不为200。	请检查传递的状态码。
EMPTY_BODY	响应体为空或不存在。	请检查服务器的响应是否正确生成，以及数据是否正确传递。
INVALID_BODY	响应体格式不正确。	请检查响应格式，并确保Content-Type头部为application/json。
INVALID_JSON	解析或验证JSON数据时出错。	请检查JSON数据的正确性，并修正语法错误。
WRONG_RESULT_FIELD	您的服务返回的响应体不符合模板。	请检查响应格式是否符合模板。
了解更多关于模板的信息
WRONG_RESULT_TIME_FIELD	响应体中的time字段不正确。	请检查响应中的时间格式。
请求检查连接
Ozon发送的内容
{
   "message_type": "string",
   "time": "2019-08-24T14:15:22Z"
}
参数
类型	形式	描述
message_type	string	—	通知类型 — TYPE_PING。
time	string	date-time	以UTC格式发送通知的日期和时间。
您的服务器应该满足什么
如果成功收到通知
如果通知被成功处理，服务器应该会发回一个HTTP 200代码的回答:

{
   "version": "string",
   "name": "string",
   "time": "2019-08-24T14:15:22Z"
}
参数
类型	形式	描述
version	string	—	程序版本。
name	string	—	程序名称。
time	string	date-time	以UTC格式发送通知的日期和时间。
如果有一个错误
如果在通知处理过程中发生错误，服务器应该会发回一个HTTP 4xx组或5xx组代码的回答:

{
   "error": {
      "code": "ERROR_UNKNOWN",
      "message": "错误",
      "details": null
   }
}
参数
类型	形式	描述
error	object	—	有关错误的信息。
code	string	—	错误代码:
• ERROR_UNKNOWN — 未知错误。
• ERROR_PARAMETER_VALUE_MISSED — 没有指定一个或多个参数的值。
• ERROR_REQUEST_DUPLICATED — 重复请求。
message	string	—	详细的错误描述。
details	string	—	更多信息。
重新发送通知
重发时间间隔
如果通知没有送达，系统会在几秒钟后尝试再发送几次请求。 两次尝试中间的间隔时间将逐渐增加。 当达到最大值10分钟时，每10分钟将会有5次多的尝试。

自动暂停发送通知
如果信息仍然不能被送达，发送请求的尝试将停止。

所有通知的发送将在满足以下任何一个条件时暂停：

服务不可用；
服务在24小时内持续返回错误；
200回复的数量比所有通知数量少两倍；
通知处理时间超过5秒钟。
如要再次接收通知，请在卖家个人中心重新确认服务URL地址。

Ozon发送的通知


订单创建通知可能会有延迟。 为了获取最新信息，请定期通过 POST /v3/posting/fbs/unfulfilled/list 方式来获取未处理货件列表。
对于每个通知类型, Ozon都会向您的服务器地址发送REST-请求。您的服务器 应该回应 按照 REST API 标准。

类型	值
TYPE_PING	在初始连接时和连接后定期检查服务器可用性状态
TYPE_NEW_POSTING	新的货件
TYPE_POSTING_CANCELLED	货件取消
TYPE_STATE_CHANGED	货件状态更改
TYPE_CUTOFF_DATE_CHANGED	货件发运日期更改
TYPE_DELIVERY_DATE_CHANGED	货件配送日期更改
TYPE_CREATE_OR_UPDATE_ITEM	商品创建和更新，或在此过程中发生的错误
TYPE_CREATE_ITEM	商品创建或商品创建错误
TYPE_UPDATE_ITEM	商品更新或更新错误
TYPE_STOCKS_CHANGED	卖家仓库库存变化
TYPE_NEW_MESSAGE	新的聊天消息
TYPE_UPDATE_MESSAGE	聊天消息更改
TYPE_MESSAGE_READ	您的消息已被买家或客服阅读
TYPE_CHAT_CLOSED	聊天已关闭
TYPE_DESCRIPTION_CATEGORY_TREE_CHANGED	类目树更改
新的发货
如果订单付款延迟，那么 in_process_at 字段 可能为空。 您可以 通过 result.in_process_at 字段中的 POST /v3/posting/fbs/get 方式查看发运日期。
通知仅适用于FBS和rFBS货件：

{
  "message_type": "TYPE_NEW_POSTING",
  "posting_number": "24319409-0021-1",
  "products": [
    {
      "sku": 147451939,
      "offer_id": "",
      "quantity": 1
    }
  ],
  "in_process_at": "2021-01-26T06:56:36.294Z",
  "warehouse_id": 12850503335000,
  "shipment_date": "2021-01-26T06:56:36.294Z",
  "tpl_integration_type": "3pl_tracking",
  "is_express": false,
  "tracking_number": "ZZV-23",
  "delivery_date_begin": "2025-01-26T06:56:36.294Z",
  "delivery_date_end": "2025-01-26T06:56:36.294Z",
  "seller_id": 1
}
参数
类型	形式	描述
message_type	string	—	通知类型 — TYPE_NEW_POSTING。
posting_number	string	—	发货货号。
products	array	—	商品信息。
sku	integer	int64	Ozon系统中的商品识别码是SKU。
quantity	integer	int64	商品数量。
in_process_at	string	date-time	商品开始处理的日期和时间, 格式为 UTC。
warehouse_id	integer	int64	仓库标识符。
shipment_date	string	date-time	必须收取货件的日期和时间。
tpl_integration_type	string	—	快递服务集成类型：
ozon —— Ozon 快递服务；
3pl_tracking —— 集成服务快递；
non_integrated —— 第三方物流服务；
aggregator —— 通过Ozon合作物流伙伴交付；
hybryd—— 俄罗斯邮政配送方案。
is_express	boolean	—	express配送标志。
tracking_number	string	—	货件跟踪号。
delivery_date_begin	string	date-time	快递开始日期和时间。
delivery_date_end	string	date-time	快递结束日期和时间。
seller_id	integer	int64	卖家识别号。
发货取消
通知仅适用于FBS和rFBS货件：

{
  "message_type": "TYPE_POSTING_CANCELLED",
  "posting_number": "24219509-0020-1",
  "products": [
    {
      "sku": 147451959,
      "quantity": 1
    }
  ],
  "old_state": "posting_transferred_to_courier_service",
  "new_state": "posting_canceled",
  "changed_state_date": "2021-01-26T06:56:36.294Z",
  "reason": {
    "id": 0,
    "message": "string"
  },
  "warehouse_id": 0,
  "seller_id": 15
}
参数
类型	形式	描述
message_type	string	—	通知类型 — TYPE_POSTING_CANCELLED。
posting_number	string	—	发货货号。
products	array	—	商品信息。
sku	integer	int64	Ozon系统中的商品识别码是SKU。
quantity	integer	int64	商品数量。
old_state	string	—	发货的上一个状态。
new_state	string	—	发货的新状态: posting_canceled — 已取消。
changed_state_date	string	date-time	发货更改的日期和时间, 格式为 UTC。
reason	object	—	取消原因的信息。
id	integer	int64	取消原因的识别号。
message	string	—	取消原因。
warehouse_id	integer	int64	储存该批发货的仓库的识别号。
seller_id	integer	int64	卖家识别号。
发货状态
posting_acceptance_in_progress — 正在验收,
posting_created — 已创建,
posting_transferring_to_delivery — 发给快递,
posting_in_carriage — 在运输途中,
posting_not_in_carriage — 未在运输中,
posting_in_client_arbitration — 快递会员仲裁,
posting_on_way_to_city — 发往城市途中,
posting_transferred_to_courier_service — 转交给快递员,
posting_in_courier_service — 快递员正在路上,
posting_on_way_to_pickup_point — 正发往取货点,
posting_in_pickup_point — 在取货点,
posting_conditionally_delivered — 暂时送到,
posting_driver_pick_up — 在司机那儿,
posting_not_in_sort_center — 集散中心未收到。
发货状态更改
匹配Seller API状态模型和推送模型状态。

Seller API		推送模型	
状态	描述	状态	描述
acceptance_in_progress	正在验收。	posting_acceptance_in_progress	正在验收。
awaiting_approve	等待确认。	posting_created	已创建。
awaiting_packaging	等待包装。	posting_created	已创建。
awaiting_registration	等待注册。	posting_awaiting_registration	等待注册。
awaiting_deliver	等待装运。	posting_transferring_to_delivery	发给快递。
posting_in_carriage	在运输途中。
posting_not_in_carriage	未在运输途中。
arbitration	仲裁。	posting_in_arbitration	仲裁。
client_arbitration	快递会员仲裁。	posting_in_client_arbitration	会员仲裁。
delivering	正在运送。	posting_on_way_to_city	发往城市途中。
posting_transferred_to_courier_service	转交给快递员。
posting_in_courier_service	快递员正在路上。
posting_on_way_to_pickup_point	正发往取货点。
posting_in_pickup_point	在取货点。
posting_conditionally_delivered	暂时送到。
driver_pickup	在司机那儿。	posting_driver_pick_up	在司机那儿。
delivered	已送达。	posting_delivered	已送达。
posting_received	已收到。
cancelled	已取消。	posting_canceled	已取消。
not_accepted	集散中心未收到。	posting_not_in_sort_center	集散中心未收到。
通知仅适用于FBS和rFBS货件：

{
  "message_type": "TYPE_STATE_CHANGED",
  "posting_number": "24219509-0020-2",
  "new_state": "posting_delivered",
  "changed_state_date": "2021-02-02T15:07:46.765Z",
  "warehouse_id": 0,
  "seller_id": 15
}
参数
类型	形式	描述
message_type	string	—	通知类型 — TYPE_STATE_CHANGED。
posting_number	string	—	发货号。
new_state	string	—	发货新状态。
changed_state_date	string	date-time	发货更改的日期和时间, 格式为 UTС。
warehouse_id	integer	int64	储存该批发货的仓库的识别号。
seller_id	integer	int64	卖家识别号。
发货状态
posting_acceptance_in_progress — 正在验收,
posting_transferring_to_delivery — 发给快递,
posting_in_carriage — 在运输途中,
posting_not_in_carriage — 未在运输途中,
posting_in_arbitration — 仲裁,
posting_in_client_arbitration — 会员快递仲裁 ,
posting_on_way_to_city — 发往城市途中,
posting_transferred_to_courier_service — 转交给快递员,
posting_in_courier_service — 快递员正在路上,
posting_on_way_to_pickup_point — 正发往取货点,
posting_in_pickup_point — 在取货点,
posting_conditionally_delivered — 暂时送到,
posting_driver_pick_up — 在司机那儿,
posting_delivered — 已送达,
posting_not_in_sort_center — 集散中心未收到。
发货的装运日起改变
通知在测试模式下工作。我们建议您使用以下方法检查发货日期 POST /v3/posting/fbs/get 在字段 result.shipment_date。

字段 new_cutoff_date 可能会出现空白, 因为快递间隔已经被删除。请等到新的日期被确定下来--后新的通知就会到来。

有时, 这种类型的通知可能在备货完成后才到达。 — 请忽略之。
通知仅适用于FBS和rFBS货件：

{
  "message_type": "TYPE_CUTOFF_DATE_CHANGED",
  "posting_number": "24219509-0020-2",
  "new_cutoff_date": "2021-11-24T07:00:00Z",
  "old_cutoff_date": "2021-11-21T10:00:00Z",
  "warehouse_id": 0,
  "seller_id": 15
}
参数
类型	形式	描述
message_type	string	—	通知类型 — TYPE_CUTOFF_DATE_CHANGED。
posting_number	string	—	发货号。
new_cutoff_date	string	date-time	新的装运日期和时间以UTC格式显示。
old_cutoff_date	string	date-time	上一个装运日期和时间以UTC格式显示。
warehouse_id	integer	int64	储存该批发货的仓库的识别号。
seller_id	integer	int64	卖家识别号。
发货快递日期改变
Ozon发送的通知:

如果货件中的商品按rFBS和FBS的模式销售，则会收到通知。

字段 new_delivery_date_begin и new_delivery_date_end 如果快递的商品在rFBS计划下出售, 将发出通知。可能会出现空百, 因为快递间隔已经被删除。等到新的日期被确定下来--后新的通知就会到来。
{
  "message_type": "TYPE_DELIVERY_DATE_CHANGED",
  "posting_number": "24219509-0020-2",
  "new_delivery_date_begin": "2021-11-24T07:00:00Z",
  "new_delivery_date_end": "2021-11-24T16:00:00Z",
  "old_delivery_date_begin": "2021-11-21T10:00:00Z",
  "old_delivery_date_end": "2021-11-21T19:00:00Z",
  "warehouse_id": 0,
  "seller_id": 15
}
参数
类型	形式	描述
message_type	string	—	通知类型 — TYPE_DELIVERY_DATE_CHANGED。
posting_number	string	—	发货号。
new_delivery_date_begin	string	date-time	新的快递开始日期和时间以UTC格式显示
new_delivery_date_end	string	date-time	新的快递结束日期和时间以UTC格式显示。
old_delivery_date_begin	string	date-time	上一个快递开始日期和时间以UTC格式显示。
old_delivery_date_end	string	date-time	上一个快递结束日期和时间以UTC格式显示。
warehouse_id	integer	int64	储存该批发货的仓库的识别号。
seller_id	integer	int64	卖家识别号。
商品创建和更新
{
    "message_type": "TYPE_CREATE_OR_UPDATE_ITEM",
    "seller_id": 0,
    "offer_id": "string",
    "product_id": 0,
    "is_error": false,
    "changed_at": "2021-09-01T14:15:22Z"
}
参数
类型	形式	描述
seller_id	integer	int64	卖家识别号。
message_type	string	—	通知类型 — TYPE_CREATE_OR_UPDATE_ITEM。
offer_id	string	—	货物代码。
product_id	integer	int64	Ozon系统中商品的标识符 — product_id。
is_error	boolean	—	在创建或更新商品过程中出现的错误标志：
• true — 出现了错误，商品未被创建或更新；
• false — 已成功创建或更新商品，无出错。
changed_at	string	date-time	更改的日期和时间。
创建商品
2023年7月15日将停止发送 TYPE_CREATE_ITEM 推送通知。
请设置您的服务以接收信息 TYPE_CREATE_OR_UPDATE_ITEM。

Ozon发送的通知:

{
    "message_type": "TYPE_CREATE_ITEM",
    "seller_id": 0,
    "offer_id": "string",
    "product_id": 0,
    "is_error": false,
    "changed_at": "2021-09-01T14:15:22Z"
}
参数
类型	形式	描述
seller_id	integer	int64	卖家识别号。
message_type	string	—	通知类型 — TYPE_CREATE_ITEM。
offer_id	string	—	货物代码。
product_id	integer	int64	Ozon系统中商品的标识符 — product_id。
is_error	boolean	—	该迹象表明创建商品时出现错误:
• true — 出现错误, 商品未创建;
• false — 商品创建无误。
changed_at	string	date-time	更改的日期和时间。
商品更新
2023年7月15日将停止发送 TYPE_CREATE_ITEM 推送通知。
请设置您的服务以接收信息 TYPE_UPDATE_ITEM。

Ozon发送的通知:

{
    "message_type": "TYPE_UPDATE_ITEM",
    "seller_id": 0,
    "offer_id": "string",
    "product_id": 0,
    "is_error": false, 
    "changed_at": "2021-09-01T14:15:22Z"
}
参数
类型	形式	描述
seller_id	integer	int64	卖家识别号。
message_type	string	—	通知类型 — TYPE_UPDATE_ITEM。
offer_id	string	—	货物代码。
product_id	integer	int64	Ozon系统中商品的标识符 — product_id。
is_error	boolean	—	该迹象表明更新商品时出现错误:
• true — 出现错误, 商品未创建;
• false — 商品创建无误。
changed_at	string	date-time	更改日期和时间。
卖家仓库库存的改变
Ozon发送的通知:

{
  "message_type": "string",
  "seller_id": 0,
  "items": [
    {
      "product_id": 0,
      "sku": 0,
      "updated_at": "2021-09-01T14:15:22Z",
      "stocks": [
        {
          "warehouse_id": 0,
          "present": 0,
          "reserved": 0
        }
      ]
    }
  ]
}
参数
类型	形式	描述
seller_id	integer	int64	卖家识别号。
message_type	string	—	通知类型 — TYPE_STOCKS_CHANGED。
items	array	—	商品数据数组。
updated_at	string	date-time	更改的日期和时间。
sku	integer	int64	在FBS或rFBS计划下工作时的Ozon系统中的商品识别码是SKU。
product_id	integer	int64	Ozon系统中商品的标识符 — product_id。
stocks	array	—	商品库存信息数组。
warehouse_id	integer	int64	仓库识别号。
present	integer	int64	仓库商品总量。
reserved	integer	int64	仓库的保留商品的数量。
新的聊天消息
{  
    "message_type": "TYPE_NEW_MESSAGE",
    "chat_id": "b646d975-0c9c-4872-9f41-8b1e57181063",
    "chat_type": "Buyer_Seller",
    "message_id": "3000000000817031942",
    "created_at": "2022-07-18T20:58:04.528Z",
    "user": {
        "id": "115568",
        "type": "Сustomer"
    },
    "data": [
        "消息文本"
    ],  
    "seller_id": "7"
}
参数
类型	形式	描述
message_type	string	—	通知类型 — TYPE_NEW_MESSAGE。
chat_id	string	—	聊天标识符。
chat_type	string	—	聊天类型：
• Seller_Support — 与客服的聊天。
• Buyer_Seller — 与买家的聊天。
• Seller_Notification — Ozon的通知。
• Seller_API_Updates — Seller API更新。
• Seller_API_Notifications — Seller API通知。
• Seller_Notification_Logistics — Ozon配送通知。
• Buyer_Seller_Select — 与Select买家的聊天。
message_id	string	—	消息标识符。
created_at	string	date-time	消息创建日期。
user	object	—	消息发送者的信息。
id	string	—	发送者标识符。
type	string	—	发送者类型：
• Customer — 买家。
• Support — 客服。
• NotificationUser — Ozon。
data	array of string	—	Markdown格式的消息内容数组。
seller_id	integer	int64	卖家标识符。
聊天消息已更改
{  
    "message_type": "TYPE_UPDATE_MESSAGE",
    "chat_id": "b646d975-0c9c-4872-9f41-8b1e57181063",
    "chat_type": "Buyer_Seller",
    "message_id": "3000000000817031942",
    "created_at": "2022-07-18T20:58:04.528Z",
    "updated_at": "2022-07-18T20:59:04.528Z",
    "user": {
        "id": "115568",
        "type": "Сustomer"
    },
    "data": [
        "消息文本"
    ], 
    "seller_id": "7"
}
参数
类型	形式	描述
message_type	string	—	通知类型 — TYPE_UPDATE_MESSAGE。
chat_id	string	—	聊天标识符。
chat_type	string	—	聊天类型：
• Seller_Support — 与客服的聊天。
• Buyer_Seller — 与买家的聊天。
• Seller_Notification — Ozon的通知。
• Seller_API_Updates — Seller API更新。
• Seller_API_Notifications — Seller API通知。
• Seller_Notification_Logistics — Ozon配送通知。
• Buyer_Seller_Select — 与Select买家的聊天。
message_id	string	—	消息标识符。
created_at	string	date-time	消息创建日期。
updated_at	string	date-time	消息更改日期。
user	object	—	消息发送者的信息。
id	string	—	发送者标识符。
type	string	—	发送者类型：
• Customer — 买家。
• Support — 客服。
• NotificationUser — Ozon。
data	array of string	—	Markdown格式的消息内容数组。
seller_id	integer	int64	卖家标识符。
您的消息已被买家或客服阅读
{  
    "message_type": "TYPE_MESSAGE_READ",
    "chat_id": "b646d975-0c9c-4872-9f41-8b1e57181063",
    "chat_type": "Buyer_Seller",
    "message_id": "3000000000817031942",
    "created_at": "2022-07-18T20:58:04.528Z",    
    "user": {
        "id": "115568",
        "type": "Сustomer"
    },
    "last_read_message_id": "3000000000817031942",
    "seller_id": "7"
}
参数
类型	形式	描述
message_type	string	—	通知类型 — TYPE_MESSAGE_READ。
chat_id	string	—	聊天标识符。
chat_type	string	—	聊天类型：
• Seller_Support — 与客服的聊天。
• Buyer_Seller — 与买家的聊天。
• Seller_Notification — Ozon的通知。
• Seller_API_Updates — Seller API更新。
• Seller_API_Notifications — Seller API通知。
• Seller_Notification_Logistics — Ozon配送通知。
• Buyer_Seller_Select — 与Select买家的聊天。
message_id	string	—	消息标识符。
created_at	string	date-time	消息创建日期。
user	object	—	阅读消息的用户信息。
id	string	—	用户标识符。
type	string	—	用户类型：
• Customer— 买家。
• Support — 客服。
• NotificationUser — Ozon。
last_read_message_id	string	—	最后阅读消息的标识符。
seller_id	integer	int64	卖家标识符。
聊天已关闭
{  
    "message_type": "TYPE_CHAT_CLOSED",
    "chat_id": "b646d975-0c9c-4872-9f41-8b1e57181063",
    "chat_type": "Buyer_Seller",
    "user": {
        "id": "115568",
        "type": "Сustomer"
    },
    "seller_id": "7"
}
参数
类型	形式	描述
message_type	string	—	通知类型 — TYPE_CHAT_CLOSED。
chat_id	string	—	聊天标识符。
chat_type	string	—	聊天类型：
• Seller_Support — 与客服的聊天。
• Buyer_Seller — 与买家的聊天。
• Seller_Notification — Ozon的通知。
• Seller_API_Updates — Seller API更新。
• Seller_API_Notifications — Seller API通知。
• Seller_Notification_Logistics — Ozon配送通知。
• Buyer_Seller_Select — 与Select买家的聊天。
user	object	—	关闭聊天的用户信息。
id	string	—	用户标识符。
type	string	—	用户类型：
• Customer— 买家。
• Support — 客服。
• NotificationUser — Ozon。
seller_id	integer	int64	卖家标识符。
类目树更改
{
  "message_type": "TYPE_DESCRIPTION_CATEGORY_TREE_CHANGED",
  "changed_at": "2026-04-07T10:27:55.955Z"
}
参数
类型	形式	描述
message_type	string	—	通知类型 — TYPE_DESCRIPTION_CATEGORY_TREE_CHANGED。
changed_at	string	date-time	类目树更改。
您服务器的回复
如果成功收到通知
如果通知被成功处理，服务器应该会发回一个HTTP 200代码的回答:

{
  "result": true
}
参数
类型	形式	描述
result	boolean	—	通知收到。
如果发生错误
如果在通知处理过程中发生错误，服务器应该会发回一个HTTP 4xx组或5xx组代码的回答:

{
  "error": {
    "code": "ERROR_UNKNOWN",
    "message": "ошибка",
    "details": null
  }
}
参数
类型	形式	描述
error	object	—	关于错误的信息。
code	string	—	错误码:
• ERROR_UNKNOWN — 不明错误。
• ERROR_PARAMETER_VALUE_MISSED — 没有指定一个或多个参数的值。
• ERROR_REQUEST_DUPLICATED — 重复请求。
message	string	—	详细的错误描述。
details	string	—	更多信息。
更新
2026年6月22日
Method	Changes
/v1/product/visibility/set	已更新方法描述。
更新了该方法请求中参数item_placement.placement的描述。
2026年6月19日
方法	变更
—	已添加模块 推送通知 → Ozon发送的通知 → 更改日期和时间。
已添加通知类型TYPE_DESCRIPTION_CATEGORY_TREE_CHANGED至推送通知 → Ozon发送的通知章节。
2026年6月11日
方法	变更
/v2/review/list	新增了用于获取评价列表的方法版本。
/v1/review/list	该方法已弃用，并将在未来停用。请切换到/v2/review/list。
—	在常见错误模块中添加了以下错误说明：
method is not allowed——适用于所有方法；
not available with existing subscription——适用于方法/v2/review/list。
2026年6月9日
方法	变更
/v4/product/info/limit	在方法的响应中添加了参数 operation_limits和total.quota_by_category的说明。
/v2/chat/list	该方式已过期，我们已将其从文件中删除。请使用 /v3/chat/list。
/v1/finance/accrual/by-day	在方法响应中，将参数名称accruals.type_id改为accruals.accrual_id。
2026年6月3日
方法	变更
/v1/carriage/create	新增了用于创建发运的方法。
/v2/posting/fbs/act/get-postings	新增了用于获取单据中货件列表的方法。
/v1/carriage/approve	新增了用于确认发运的方法。
/v2/posting/fbs/act/get-container-labels	新增了方法，用于创建货位标签。
2026年6月2日
方法	变更
/v1/seller/info	更新了方法响应中company.currency参数的描述。
/v1/polygon/create	已更新方法描述。
2026年5月28日
方法	变更
/v5/product/info/prices	更新了方法响应中items.marketing_actions、items.marketing_actions.actions、items.marketing_actions.actions.date_from、items.marketing_actions.actions.date_to、items.marketing_actions.actions.title和items.marketing_actions.actions.value参数的说明。
/v1/fbp/draft/get
/v1/fbp/archive/get
/v1/fbp/order/get	更新了方法响应中delivery_details.direct_details.timeslot_details.timeslot.timeslot_end、delivery_details.direct_details.timeslot_details.timeslot.timeslot_start、delivery_details.drop_off_point.timeslot.timeslot_end和delivery_details.drop_off_point.timeslot.timeslot_start参数的说明。
/v1/fbp/draft/list
/v1/fbp/archive/list
/v1/fbp/order/list	更新了方法响应中items.delivery_details.direct_details.timeslot_details.timeslot.timeslot_end、items.delivery_details.direct_details.timeslot_details.timeslot.timeslot_start、items.delivery_details.drop_off_point.timeslot.timeslot_end和items.delivery_details.drop_off_point.timeslot.timeslot_start参数的说明。
2026年5月26日
方法	变更
—	在常见错误模块中，更新了错误FLAMMABLE_ONLY_ON_SELF_OR_PROVIDER_DELIVERY在方法/v2/products/stocks中的描述。
2026年5月22日
方法	变更
/v2/delivery-method/list	在方法响应中新增了参数delivery_methods.tpl_dropoff_point。
—	在错误板块中，已为/v3/product/import方式添加了错误描述：SELLER_NO_CONTRACT_FAILED, error_attribute_values_empty, error_attribute_values_out_of_range, missing_dimension, VALUE_MAX_LIMIT, EMPTY_REQUIRED, description_category_invalid, description_category_has_no_description_type, description_category_is_legacy, levels_category_not_found, description_category_is_empty, description_type_is_empty, vat_invalid, name_too_long, all_image_failed, invalid_rich_content_json, all_image_unprocessed, price_out_of_range, old_price_less_than_price, min_auto_price_too_big, min_auto_price_too_small和price_less_than_min_auto_price。
2026年5月21日
方法	变更
/v2/finance/realization	已更新方法说明。
—	在错误部分中，为方法/v2/posting/fbs/package-label新增了错误label not allowed for delivered postings的说明。
2026年5月20日
方法	变更
/v1/seller-actions/products/delete	更新了该方法请求中参数skus的描述。
/v1/finance/balance	更新了请求示例。
2026年5月19日
方法	变更
/v1/analytics/data	更新了该方法请求中参数dimension的描述。
/v1/product/prices/details	在方法响应中：
新增了prices.price_indexes参数；
将prices.discount_percent参数标记为已弃用；
更新了prices.customer_price参数的说明。
/v1/analytics/average-delivery-time/details
/v1/analytics/average-delivery-time
/v1/analytics/average-delivery-time/summary	该方式已过期，我们已将其从文件中删除。
/v1/question/answer/list	在方法响应中新增了answers.status_publication参数。
/v1/question/list	在方法请求中新增了sort_dir和limit参数。
在方法响应中新增了has_next参数。
2026年5月15日
方法	变更
/v1/barcode/add	已更新方法描述。
2026年5月14日
方法	变更
/v2/product/info/stocks-by-warehouse/fbs	新增了用于处理卖家仓库库存的方法。
/v3/product/info/list	已更新方法响应中items.promotions.type参数的描述。
/v3/product/import	更新了该方法请求中参数items.promotions.type的描述。
/v1/finance/cash-flow-statement/list	更新了方法描述。
2026年5月12日
方法	变更
/v1/actions	新增了参数result.auto_add_dates至方法响应中。
/v1/actions/auto-add/products/list
/v1/actions/auto-add/products/candidates
/v1/actions/auto-add/products/delete
/v1/actions/auto-add/products/update	新增了用于处理商品自动添加到促销活动中的beta方法。
/v1/product/visibility/info	新增了用于获取商品可见性信息的Beta版方法。
/v3/product/import
/v1/product/import-by-sku
/v1/product/attributes/update
/v1/product/pictures/import
/v1/product/update/offer-id	已更新方法说明。
/v5/product/info/prices	已新增方法描述。
/v1/product/import/prices	更新了该方法请求中参数prices.min_price的描述。
/v1/product/action/timer/status	已更新方法响应中statuses.expired_at参数的描述。
2026年5月7日
方法	变更
—	在推送通知 → 新的聊天消息、聊天消息已更改、您的消息已读和聊天已关闭部分中，已更新参数chat_type的取值。
2026年5月6日
方法	变更
/v1/finance/accrual/postings	新增用于获取按货件统计的应计项目的beta方法。
/v1/finance/accrual/types	新增用于获取按货件统计的应计项目参考信息的beta方法。
/v1/finance/accrual/by-day	新增用于获取某一货件某日应计项目的beta方法。
/v3/finance/transaction/list	该方法即将废弃，并将于2026年7月6日停用。请切换到/v1/finance/accrual/postings, /v1/finance/accrual/types, /v1/finance/accrual/by-day。
/v3/finance/transaction/totals	该方法即将废弃，并将于2026年7月6日停用。请切换到/v1/finance/accrual/postings, /v1/finance/accrual/types, /v1/finance/accrual/by-day。
2026年5月5日
方法	变更
/v4/product/info/stocks
/v5/product/info/prices	更新了方法请求中参数 filter.visibility 的描述。
/v1/seller/ozon-logistics/info	更新了方法名称。
更新了方法响应中参数ozon_logistics_enabled的说明。
/v3/posting/fbs/get	更新了方法响应中参数result.analytics_data.client_delivery_date_begin和result.analytics_data.client_delivery_date_end的说明。
/v3/posting/fbs/list
/v3/posting/fbs/unfulfilled/list	更新了各方法响应中参数 result.postings.analytics_data.client_delivery_date_begin和 result.postings.analytics_data.client_delivery_date_end的说明。
/v3/chat/list	更新了方法响应中参数chats.chat.chat_type的说明。
/v3/product/import
/v1/product/pictures/import
/v1/product/archive
/v1/product/unarchive
/v1/product/info/wrong-volume	更新了方法描述。
2026年4月30日
方法	变更
/v4/posting/fbs/unfulfilled/list	新增了用于获取FBS未处理货件列表的方法新版本。
/v3/posting/fbs/unfulfilled/list	该方法已弃用，并将于2026年6月1日停用。请切换到 /v4/posting/fbs/unfulfilled/list。
/v4/posting/fbs/list	新增了用于获取FBS货件列表的方法新版本。
/v3/posting/fbs/list	该方法已弃用，并将于2026年6月1日停用。请切换到 /v4/posting/fbs/list。
/v1/posting/fbp/list	新增了用于获取FBP货件列表的新的beta方法。
/v1/carriage/get	更新了方法响应中 available_actions 参数的描。
/v1/warehouse/list	在方式的请求中添加了with.able_to_set_price参数。
在方式的响应中添加了result.is_able_to_set_price和result.is_presorted参数。
/v3/posting/fbs/unfulfilled/list	在方式的请求中添加了filter.last_changed_status_date参数。
在方式的响应中添加了result.postings.is_presortable、result.postings.destination_place_id、result.postings.destination_place_name和result.postings.customer.customer_email参数。
/v3/posting/fbs/list	在方式的响应中添加了参数result.postings.is_presortable、result.postings.destination_place_id、result.postings.destination_place_name和result.postings.customer.customer_email。
2026年4月24日
方法	变更
/v1/fbp/draft/drop-off/product/validate
/v1/fbp/draft/direct/product/validate
/v1/fbp/draft/pick-up/product/validate	在方法的响应中，为rejected_items.rejection_reasons参数增加了NO_SALES、SURPLUS和AVAILABILITY_IS_EMPTY值。
/v1/fbp/draft/drop-off/registrate
/v1/fbp/draft/direct/registrate
/v1/fbp/draft/pick-up/registrate	在方法的响应中，为error.bundle_errors.errors参数增加了NO_SALES、SURPLUS和AVAILABILITY_IS_EMPTY值。
2026年4月17日
方法	变更
/v3/posting/fbs/get	在方法响应中新增参数result.tariffication_steps。
/v3/posting/fbs/list
/v3/posting/fbs/unfulfilled/list	在各方法响应中新增参数result.postings.tariffication_steps。
2026年4月14日
方法	变更
/v1/seller-actions/create/ozon-card-discount
/v1/seller-actions/update/ozon-card-discount	该促销活动已无法使用，相关方法已从文档中删除。
2026年4月6日
方法	变更
/v1/product/visibility/set	新增了用于设置商品在Ozon和Ozon Select橱窗可见性的Beta方法。
2026年4月2日
方法	变更
—	已将方法操作顺序 → 请参加促销活动模块更名为方法操作顺序 → 参与Ozon促销活动。
已将促销活动模块更名为Ozon促销活动。
新增了方法操作顺序 → 卖家促销活动管理模块。
新增了卖家促销活动模块描述。
2026年3月31日
方法	变更
/v1/review/comment/create
/v1/review/comment/delete
/v1/review/comment/list
/v1/review/change-status
/v1/review/count
/v1/review/info
/v1/review/list	更新了方法描述。
2026年3月24日
方法	变更
/v1/seller-actions/create/discount
/v1/seller-actions/create/discount-with-condition
/v1/seller-actions/create/installment
/v1/seller-actions/create/multi-level-discount
/v1/seller-actions/create/ozon-card-discount
/v1/seller-actions/create/voucher
/v1/seller-actions/update/discount
/v1/seller-actions/update/discount-with-condition
/v1/seller-actions/update/installment
/v1/seller-actions/update/multi-level-discount
/v1/seller-actions/update/ozon-card-discount
/v1/seller-actions/update/voucher
/v1/seller-actions/products/add
/v1/seller-actions/products/candidates
/v1/seller-actions/products/delete
/v1/seller-actions/products/list
/v1/seller-actions/archive
/v1/seller-actions/change-activity
/v1/seller-actions/list
/v1/seller-actions/voucher/get	新增了用于管理卖家促销活动的Beta方法。
/v1/seller/info	新增用于获取卖家个人中心信息的测试版方法。
/v1/seller/ozon-logistics/info	新增用于获取卖家接入 Ozon 物流情况的测试版方法。
/v1/delivery-method/list	该方式已过时，并将于2026年4月7日关闭。请切换至 /v2/delivery-method/list 新版本。
/v1/warehouse/list	该方式已过时，并将于2026年4月7日关闭。请切换至 /v2/warehouse/list 新版本。
/v3/product/import	在方法描述中新增了关于上传Ozon Select主图的信息。
2026年3月19日
方法	变更
/v3/chat/list	更新了方法响应中 chats.chat.chat_type 参数的描。
/v3/chat/history	更新了方法响应中 messages.user.type 参数的描。
2026年3月17日
方法	变更
/v1/description-category/tips	我们新增了用于获取商品类目提示的Beta方法。
/v1/analytics/average-delivery-time
/v1/analytics/average-delivery-time/details
/v1/analytics/average-delivery-time/summary	更新了方法描述。
/v1/product/update/offer-id	更新了方法描述。为方法请求中的 update_offer_id 参数添加了限制。
/v3/posting/fbs/get	已更新方式响应中 result.analytics_data.client_delivery_date_begin 和 result.analytics_data.client_delivery_date_end 参数的描述。
/v3/posting/fbs/list
/v3/posting/fbs/unfulfilled/list	更新了方法响应中 result.postings.analytics_data.client_delivery_date_begin 和 result.postings.analytics_data.client_delivery_date_end 参数的描述。
2026年3月12日
方法	变更
/v1/carriage/get	已更新方法响应中 available_actions 参数的描述。
2026年3月11日
方法	变更
/v3/chat/list	更新了方法响应中的 chats.chat.chat_type 参数描述。
2026年3月10日
方法	变更
/v2/posting/fbs/package-label	更新了方法描述。
2026年3月4日
方法	变更
/v2/delivery-method/list	新增了用于获取rFBS仓库配送方式的方法。
/v1/roles	在方法的响应中新增了参数 expires_at。
/v1/product/prices/details	已将该方法从Beta版迁移至正式版。
—	在 授权 模块，我们更新了关于API密钥使用的信息。
2026年2月26日
方法	变更
/v3/product/info/list	已更新方法响应中 items.is_kgt 参数的描述。
—	在 方法操作顺序 → 获取库存信息 部分，更新了有关方法操作的描述。
2026年2月20日
方法	变更
/v2/chat/history	该方式已过期，我们已将其从文件中删除。请使用 /v3/chat/history。
2026年2月17日
方法	变更
/v1/fbp/draft/direct/seller-dlv/create	在方法请求中将该参数 bundle_id, delivery_details, delivery_details.driver_name, delivery_details.timeslot_start, delivery_details.vehicle_number, delivery_details.vehicle_type, package_units_count 和 warehouse_id 标记为必填。
/v1/fbp/draft/direct/seller-dlv/edit	在方法请求中将该参数 driver_name, row_version, supply_id, vehicle_number 和 vehicle_type 标记为必填。
/v1/fbp/draft/direct/timeslot/edit	在方法请求中将该参数 row_version, supply_id 和 timeslot_start 标记为必填。
/v1/fbp/draft/direct/timeslot/get	在方法请求中将该参数 bundle_id, interval_end, interval_start 和 warehouse_id 标记为必填。
/v1/fbp/draft/direct/create	在方法请求中将该参数 bundle_id, delivery_details, delivery_details.timeslot_start, package_units_count 和 warehouse_id 标记为必填。
/v1/fbp/draft/direct/delete
/v1/fbp/draft/drop-off/delete
/v1/fbp/draft/pick-up/delete
/v1/fbp/order/direct/cancel	在方法请求中将该参数 supply_id 标记为必填。
/v1/fbp/draft/direct/product/validate
/v1/fbp/draft/drop-off/product/validate
/v1/fbp/draft/pick-up/product/validate	已将方法请求中的 skus, skus.count, skus.sku 和 warehouse_id 参数标记为必填。
/v1/fbp/draft/direct/registrate
/v1/fbp/draft/drop-off/registrate
/v1/fbp/draft/pick-up/registrate	已将方法请求中的 row_version 和 supply_id 参数标记为必填。
/v1/fbp/draft/direct/tpl-dlv/create	在方法请求中将该参数 bundle_id, delivery_details, delivery_details.timeslot_start, delivery_details.tracking_number, delivery_details.transport_company_name, package_units_count 和 warehouse_id 标记为必填。
/v1/fbp/draft/direct/tpl-dlv/edit	在方法请求中将该参数 row_version, supply_id, tracking_number 和 transport_company_name 标记为必填。
/v1/fbp/draft/drop-off/create	在方法请求中将该参数 bundle_id, delivery_details, delivery_details.drop_off_date, delivery_details.drop_off_point_id, delivery_details.drop_off_province_uuid, package_units_count 和 warehouse_id 标记为必填。
/v1/fbp/draft/drop-off/dlv/edit	在方法请求中将该参数 drop_off_date, drop_off_point_id, drop_off_province_uuid, row_version 和 supply_id 标记为必填。
/v1/fbp/draft/drop-off/province/list	在方法请求中将该参数 warehouse_id 标记为必填。
/v1/fbp/draft/drop-off/point/list	在方法请求中将该参数 page_size, province_uuid 和 warehouse_id 标记为必填。
/v1/fbp/draft/drop-off/point/timetable	在方法请求中将该参数 drop_off_point_id, province_uuid 和 warehouse_id 标记为必填。
/v1/fbp/draft/pick-up/create	在方法请求中将该参数 bundle_id, delivery_details, delivery_details.address, delivery_details.comment, delivery_details.date, delivery_details.sender_name, delivery_details.sender_phone, package_units_count 和 warehouse_id 标记为必填。
/v1/fbp/draft/pick-up/dlv/edit	在方法请求中将该参数 row_version, supply_id, pickup_details, pickup_details.address, pickup_details.comment, pickup_details.date, pickup_details.sender_name 和 pickup_details.sender_phone 标记为必填。
2026年2月16日
方法	变更
/v1/warehouse/list	该方式已过时，并将于2026年3月20日关闭。请切换至 /v2/warehouse/list 新版本。
/v1/posting/carriage-available/list	该方式已过时，并将于2026年3月20日关闭。请切换至 /v2/carriage/delivery/list 新版本。
2026年2月12日
方法	变更
/v1/returns/rfbs/action/set	在方法请求中将该参数 return_id 标记为必填。
/v1/chat/send/file	在方法请求中将该参数 base64_content 和 name 标记为必填。
/v1/report/postings/create	在方法请求中将该参数 filter.processed_at_from 和 filter.processed_at_to 标记为必填。
/v1/report/marked-products-sales/create	在方法请求中将该参数 date.from 和 date.to 标记为必填。
/v1/product/info/wrong-volume	在方法请求中将该参数 limit 标记为必填。
/v1/product/stairway-discount/by-quantity/set	在方法请求中将该参数 stairways，stairways.enabled，stairways.sku，stairways.stairway，stairways.stairway.steps，stairways.stairway.steps.discount，stairways.stairway.steps.quantity 和 stairways.stairway.steps.step 标记为必填。
/v1/product/stairway-discount/by-quantity/get
/v1/product/prices/details	在方法请求中将该参数 skus 标记为必填。
/v1/warehouse/fbs/update	在方法请求中将该参数 address_coordinates 标记为必填。
/v1/actions/discounts-task/list	在方法请求中将该参数 page 标记为必填。
/v1/fbp/draft/get	在方法请求中将该参数 supply_id 标记为必填。
/v1/fbp/draft/list	在方法请求中将该参数 count 标记为必填。
/v1/fbp/order/direct/seller-dlv/edit	在方法请求中将该参数 driver_name，row_version，supply_id，vehicle_number 和 vehicle_type 标记为必填。
/v1/fbp/order/direct/timeslot/edit	在方法请求中将该参数 row_version，supply_id 和 timeslot_start 标记为必填。
/v1/fbp/order/direct/timeslot/list	在方法请求中将该参数 interval_end，interval_start 和 supply_id 标记为必填。
/v1/fbp/order/drop-off/cancel
/v1/fbp/order/pick-up/cancel
/v1/fbp/act-from/create
/v1/fbp/act-to/create
/v1/fbp/order/get	在方法请求中将该参数 supply_id 标记为必填。
/v1/fbp/order/drop-off/dlv/edit	在方法请求中将该参数 drop_off_date，row_version 和 supply_id 标记为必填。
/v1/fbp/order/drop-off/timetable	在方法请求中将该参数 drop_off_point_id，province_uuid 和 warehouse_id 标记为必填。
/v1/fbp/order/pick-up/dlv/edit	在方法请求中将该参数 pickup_details，pickup_details.sender_name，pickup_details.sender_phone，row_version 和 supply_id 标记为必填。
/v1/fbp/act-from/get	在方法请求中将该参数 file_uuid 标记为必填。
/v1/fbp/act-to/get	在方法请求中将该参数 code 和 supply_id 标记为必填。
/v1/fbp/order/list	在方法请求中将该参数 count 标记为必填。
/v1/product/import-by-sku	更新了方法描述。
2026年2月10日
方法	变更
/v3/product/list
/v4/product/info/attributes
/v4/product/info/stocks
/v5/product/info/prices	更新了方法请求中参数 filter.visibility 的描述。
2026年2月5日
方法	变更
/v2/posting/fbs/get-by-barcode	在方法请求中将该参数 barcode 标记为必填。
/v2/posting/fbs/cancel	在方法请求中将该参数 cancel_reason_id 和 posting_number 标记为必填。
/v6/fbs/posting/product/exemplar/set	在方法请求中将该参数 posting_number，products，products.product_id，products.exemplars 和 products.exemplars.exemplar_id 标记为必填。
/v6/fbs/posting/product/exemplar/create-or-get
/v5/fbs/posting/product/exemplar/status
/v1/fbs/posting/product/exemplar/update	在方法请求中将该参数 posting_number 标记为必填。
/v5/fbs/posting/product/exemplar/validate	在方法请求中将该参数 posting_number，products.product_id 和 products.exemplars 标记为必填。
/v1/carriage/set-postings	在方法请求中将该参数 carriage_id 和 posting_numbers 标记为必填。
/v1/carriage/cancel	在方法请求中将该参数 carriage_id 标记为必填。
/v1/assembly/carriage/posting/list
/v1/assembly/carriage/product/list	在方法请求中将该参数 filter.carriage_id 标记为必填。
/v1/assembly/fbs/posting/list	在方法请求中将该参数 sort_dir，filter.cutoff_from 和 filter.cutoff_to 标记为必填。
/v1/assembly/fbs/product/list	在方法请求中将该参数 filter.cutoff_from 和 filter.cutoff_to 标记为必填。
2026年2月3日
方法	变更
/v1/report/info	已更新方法响应中 result.report_type 参数的描述。
/v1/report/list	更新了方法请求中的 report_type 参数描述。
已更新方法响应中 result.reports.report_type 参数的描述。
2026年2月2日
方法	变更
/v1/rating/index/fbs/info
/v1/rating/index/fbs/posting/list
/v2/warehouse/list
/v1/warehouse/operation/status
/v1/warehouse/archive
/v1/warehouse/unarchive
/v1/warehouse/invalid-products/get
/v1/warehouse/warehouses-with-invalid-products
/v1/warehouse/fbs/create/drop-off/list
/v1/warehouse/fbs/create/drop-off/timeslot/list
/v1/warehouse/fbs/create/pick-up/timeslot/list
/v1/warehouse/fbs/create
/v1/warehouse/fbs/first-mile/update
/v1/warehouse/fbs/update/drop-off/list
/v1/warehouse/fbs/update/drop-off/timeslot/list
/v1/warehouse/fbs/update/pick-up/timeslot/list
/v1/warehouse/fbs/update	已将该方法从Beta版迁移至正式版。
2026年1月29日
方法	变更
/v2/actions/discounts-task/list	新增用于获取折扣申请列表的 beta 方法。
/v1/actions/discounts-task/list	该方法已弃用，并将在未来停用。请切换至 /v2/actions/discounts-task/list。
2026年1月27日
方法	变更
/v1/supply-order/bundle	增加了获取交付物成分的方法。
—	在 管理订单 → FBP 方案 部分，更新了有关方法操作的描述。
/v4/product/info/stocks	更新了方法描述。
2026年1月26日
方法	变更
/v3/posting/fbs/get	在方法响应中添加了 result.fact_delivery_date 和 result.financial_data.products.customer_currency_code 参数。
/v3/posting/fbs/list
/v3/posting/fbs/unfulfilled/list	在方法响应中添加了 result.postings.financial_data.products.customer_currency_code 参数。
2026年1月22日
方法	变更
/v1/report/products/create	更新了方法请求中的 visibility 参数描述。
2026年1月20日
方法	变更
/v2/fbs/posting/sent-by-seller	该方式已过期，我们已将其从文件中删除。
—	在 方法操作顺序 → 管理订单 → rFBS Crossborder 模式 和 方法操作顺序 → 管理订单 → rFBS Crossborder 模式含集成物流服务 部分，更新了有关方法操作的描述。
2026年1月16日
方法	变更
/v1/question/answer/create
/v1/question/answer/delete
/v1/question/answer/list
/v1/question/change-status
/v1/question/count
/v1/question/info
/v1/question/list
/v1/question/top-sku	新增用于处理问题和回答的 Beta 方法。
/v1/report/info	已更新方法响应中 result.report_type 参数的描述。
/v1/report/list	已更新方法请求中 report_type 参数以及方法响应中 result.reports.report_type 的描述。
2026年1月15日
方法	变更
/v1/product/prices/details	新增用于获取商品价格详细信息的 Beta 方法。
2026年1月13日
方法	变更
/v5/fbs/posting/product/exemplar/create-or-get
/v4/fbs/posting/product/exemplar/set
/v5/fbs/posting/product/exemplar/set
/v4/fbs/posting/product/exemplar/status
/v4/fbs/posting/product/exemplar/validate	方法已过时， 已从 文档中移除。
2025年12月30日
方法	变更
/v1/report/marked-products-sales/create
/v1/assembly/carriage/posting/list
/v1/assembly/carriage/product/list
/v1/assembly/fbs/posting/list
/v1/assembly/fbs/product/list	已将该方法从Beta版迁移至正式版。
/v1/product/import/prices	更新了方法请求中的 prices.vat 参数描述。
/v1/product/import-by-sku
/v3/product/import	更新了方法请求中的 items.vat 参数描述。
2025年12月26日
方法	变更
/v2/returns/rfbs/list
/v2/returns/rfbs/list	参数 returns.client_name 即将废弃，将于2026年2月2日停止支持。
2025年12月25日
方法	变更
/v2/finance/realization	已从方法响应中移除参数 result.header.doc_amount 和 result.header.vat_amount。
/v1/finance/realization/posting	已从方法响应中移除参数 header.doc_amount 和 header.vat_amount。
/v5/fbs/posting/product/exemplar/status	已更新方法响应中参数 products.exemplars.marks.check_status 的说明。
/v5/product/info/prices	已更新方法响应中参数 items.price.retail_price 的说明。
/v3/product/list	更新了方法响应中的 result.items.quants 参数描述。
/v2/posting/fbs/product/change	该方式已过期，我们已将其从文件中删除。
2025年12月23日
方法	变更
/v1/product/unarchive	更新了方法请求中的 product_id 参数描述。
/v1/actions/candidates
/v1/actions/products	添加了参数 result.products.current_boost、result.products.price_min_elastic、result.products.price_max_elastic、result.products.min_boost 和 result.products.max_boost 到方法的响应中。
2025年12月19日
方法	变更
/v1/warehouse/invalid-products/get	新增用于获取 rFBS 配送受限商品列表的 beta 方法。
2025年12月18日
方法	变更
/v2/warehouse/list	在方法请求中添加了参数 limit 和 cursor。
在方法的响应中添加了参数 cursor。
/v1/warehouse/list	在方法请求中添加了参数 limit 和 offset。
/v4/product/info/stocks	已将方法响应中的参数 items.stocks.warehouse_ids 标记为已弃用。
/v1/report/warehouse/stock	更新了方法请求中的 warehouseId 参数描述。
2025年12月16日
方法	变更
/v5/fbs/posting/product/exemplar/status	更新了方法响应中的 status 参数描述。
/v2/warehouse/list	在方法的响应中添加了参数 has_next 和 cursor。
2025年12月15日
方法	变更
/v1/warehouse/warehouses-with-invalid-products	新增用于获取在 rFBS 配送中存在配送限制商品的仓库列表的 beta 方法。
2025年12月12日
方法	变更
/v1/finance/balance	新增了用于获取余额报告的测试版方法。
/v1/finance/cash-flow-statement/list	更新了方法描述。
/v1/returns/list	移除了方法请求中参数 filter.visual_status_name 的值 ReturnCompensated。
2025年12月4日
方法	变更
/v1/product/stairway-discount/by-quantity/set	新增用于管理按商品数量计算折扣的测试方法。
/v1/product/stairway-discount/by-quantity/get	新增用于获取按商品数量计算折扣信息的测试方法。
2025年11月27日
方法	变更
/v3/posting/fbs/get	在方法的响应中：
更新了参数 result.analytics_data.payment_type_group_name 的描述；
新增了参数 result.analytics_data.client_delivery_date_begin 和result.analytics_data.client_delivery_date_end。
/v3/posting/fbs/list
/v3/posting/fbs/unfulfilled/list	在各方法的响应中：
更新了参数 result.analytics_data.payment_type_group_name 的描述；
新增了参数 result.analytics_data.client_delivery_date_begin 和result.analytics_data.client_delivery_date_end。
2025年11月25日
方法	变更
/v1/product/attributes/update	更新了方法描述。
2025年11月21日
方法	变更
—	添加了用于处理FBP交付的测试方法。
/v4/product/info/stocks	更新了方法响应中的 items.stocks.type 参数描述。
2025年11月20日
方法	变更
/v1/rating/index/fbs/info
/v1/rating/index/fbs/posting/list	新增了用于处理错误指数的测试版方法：FBS 和 rFBS。
/v1/returns/list	已添加参数 filter.compensation_status_id到方法请求。
在方法的响应中添加了参数 returns.compensation_status。
2025年11月18日
方法	变更
/v1/report/info	在方法的响应中新增参数result.expires_at。
/v1/report/list	在方法的响应中新增参数result.reports.expires_at。
2025年11月13日
方法	变更
/v1/analytics/average-delivery-time
/v1/analytics/average-delivery-time/details
/v1/analytics/average-delivery-time/summary	已将该方法从Beta版迁移至正式版。
2025年11月12日
方法	变更
/v3/product/info/list	在方法回答该items.marketing_price参数已过期，我们已将其从文件中删除。
/v5/product/info/prices	在方法回答该price.marketing_price参数已过期，我们已将其从文件中删除。
2025年11月11日
方法	变更
/v1/search-queries/text
/v1/search-queries/top	已新增用于处理搜索查询的方法。
/v1/analytics/data
/v1/chat/send/message
/v1/chat/send/file
/v1/chat/start
/v3/chat/history
/v2/chat/read
/v1/finance/realization/by-day
/v1/review/comment/create
/v1/review/comment/delete
/v1/review/comment/list
/v1/review/change-status
/v1/review/count
/v1/review/info
/v1/review/list	更新了方法描述。
2025年11月1日
方法	变更
/v1/assembly/carriage/posting/list
/v1/assembly/carriage/product/list
/v1/assembly/fbs/posting/list
/v1/assembly/fbs/product/list	已新增用于处理 FBS 拣货单的测试方法。
2025年10月28日
方法	变更
/v1/report/postings/create	更新了方法请求中的 filter.is_express 参数描述。
2025年10月23日
方法	变更
/v1/report/marked-products-sales/create	添加了用于获取带有标记商品销售报告的beta方法。
/v1/product/info/stocks-by-warehouse/fbs	在请求中添加了参数 offer_id，并在方法响应中添加了参数 results.offer_id。
/v3/posting/fbs/get	更新了方法响应中的 result.substatus 参数描述。
/v3/posting/fbs/unfulfilled/list
/v3/posting/fbs/list	更新了方法响应中的 result.postings.substatus 参数描述。
/v4/posting/fbs/ship
/v4/posting/fbs/ship/package	更新了方法描述。
—	在错误板块中，已为/v1/product/import/prices方式添加了错误描述：error limiting: acquire limit per item: items limit: limit exceeded。
2025年10月22日
方法	变更
/v1/product/import/prices	已添加参数 prices.manage_elastic_boosting_through_price 到方法请求。
2025年10月21日
方法	变更
/v3/posting/fbs/list
/v3/posting/fbs/unfulfilled/list	在方法的响应中添加了参数result.postings.shipment_date_without_delay。
/v3/posting/fbs/get	在方法的响应中添加了参数 result.shipment_date_without_delay。
—	在 Ozon发送的通知 → 新的发货 部分中，更新了新发货通知的示例。
2025年10月17日
方法	变更
/v1/warehouse/fbs/create/drop-off/timeslot/list
/v1/warehouse/fbs/update/drop-off/timeslot/list
/v1/warehouse/fbs/create/pick-up/timeslot/list
/v1/warehouse/fbs/update/pick-up/timeslot/list	新增了与时间段相关的Beta方法。
/v2/warehouse/list	已在方式响应中添加了 warehouses.cut_in_time, warehouses.warehouse_type, warehouses.is_comfort 和 warehouses.is_express 参数。
/v1/warehouse/fbs/create
/v1/warehouse/fbs/first-mile/update	在方法请求中新增了参数 cut_in_time 和 timeslot_id。
2025年10月16日
方法	变更
/v3/posting/fbs/get	更新了方法响应中的 result.financial_data.products.product_id 参数描述。
/v3/posting/fbs/list
/v3/posting/fbs/unfulfilled/list	更新了方法响应中的 result.postings.financial_data.products.product_id 参数描述。
/v5/fbs/posting/product/exemplar/validate	更新了方法响应中的 products.exemplars.marks 参数描述。
/v6/fbs/posting/product/exemplar/set	更新了方法请求中的 products.exemplars.marks 参数描述。
/v2/posting/fbs/get-by-barcode	已从方法响应中移除参数 result.analytics_data 和 result.financial_data。
/v3/chat/list	更新了该方法请求中参数 limit 的描述。
2025年10月14日
方法	变更
/v2/finance/realization	参数 result.header.doc_amount 和 result.header.vat_amount即将废弃，将于2025年12月14日停止支持。
/v1/finance/realization/posting	参数 header.doc_amount 和 header.vat_amount即将废弃，将于2025年12月14日停止支持。
2025年10月10日
方法	变更
/v5/product/info/prices	已在方式响应中添加了items.comissions.sales_percent_fbp和items.comissions.sales_percent_rfbs参数。
在方法响应的 SUPER 参数中，新增可能的值 items.price_indexes.color_index。
/v3/product/info/list	在方法响应的 COLOR_INDEX_SUPER 参数中，新增可能的值 items.price_indexes.color_index。
2025年10月9日
方法	变更
—	新增了以下模块的说明： 与买家的聊天，分析报告 和 财务报告。
2025年10月8日
方法	变更
/v3/product/import	更新了该方法请求中参数 items.images 的描述。
更新了方法描述。
/v1/product/pictures/import	更新了该方法请求中参数 images 的描述。
更新了方法描述。
2025年10月7日
方法	变更
/v1/product/info/warehouse/stocks	新增了用于获取FBS和rFBS仓库库存的测试方法。
2025年10月6日
方法	变更
/v3/product/info/list	参数 items.marketing_price 即将废弃，我们将于2025年11月12日关闭该参数。
/v5/product/info/prices	参数 items.price.marketing_price 即将废弃，我们将于2025年11月12日关闭该参数。
/v3/product/info/list	在方法响应中新增了参数 items.availabilities。
2025年10月1日
方法	变更
/v3/product/import	更新了该方法请求中参数 items.name 的描述。
2025年9月29日
方法	变更
v3/posting/fbs/unfulfilled/list
v3/posting/fbs/list	已在方式响应中添加了result.postings.products.imei和result.postings.requirements.products_requiring_imei参数。
v3/posting/fbs/get	已在方式响应中添加了result.products.has_imei, result.product_exemplars.products.exemplars.imei和result.requirements.products_requiring_imei参数。
v6/fbs/posting/product/exemplar/create-or-get	已更新products.exemplars.marks参数的描述，并在方式响应中添加了products.has_imei参数。
v5/fbs/posting/product/exemplar/validate	已更新该方式的请求与响应中products.exemplars.marks参数的描述。
v6/fbs/posting/product/exemplar/set	更新了方法请求中products.exemplars.marks参数的描述。
v5/fbs/posting/product/exemplar/status	更新了方法响应中products.exemplars.marks参数的描述。
/v1/report/postings/create	已添加以下参数filter.warehouse_id、filter.delivery_method_id、filter.is_express、with.additional_data、with.analytics_data、with.customer_data 和 with.jewelry_codes 到方法请求。
2025年9月24日
方法	变更
/v2/returns/rfbs/list	更新了该方法请求中参数 last_id 的描述。
已更新回答示例。
/v2/returns/rfbs/get
/v4/product/info/stocks	已更新响应示例。
/v3/posting/fbs/unfulfilled/list	更新了方法响应中的 result.postings.status 和 result.postings.substatus 参数描述。
/v3/posting/fbs/get	在方法响应中：
• 添加了参数 result.financial_data.products.customer_price；
• 更新了 result.requirements.products_requiring_gtd、result.requirements.products_requiring_mandatory_mark、result.requirements.products_requiring_jw_uin、result.requirements.products_requiring_rnpt、result.status、result.substatus、result.previous_substatus、result.financial_data.products.price、result.financial_data.products.old_price、result.customer.phone、result.addressee.phone 和 result.products.is_marketplace_buyout 参数描述。
/v3/posting/fbs/list	在方法响应中：
• 添加了参数 result.postings.financial_data.products.customer_price；
• 更新了 result.postings.requirements.products_requiring_gtd、result.postings.requirements.products_requiring_mandatory_mark、result.postings.requirements.products_requiring_jw_uin、result.postings.requirements.products_requiring_rnpt、result.postings.financial_data.products.price、result.postings.financial_data.products.old_price、result.postings.customer.phone、result.postings.addressee.phone 和 result.postings.products.is_marketplace_buyout 参数描述。
/v3/posting/fbs/unfulfilled/list	更新了方法响应中的 result.postings.customer.phone、result.postings.addressee.phone、result.postings.products.is_marketplace_buyout 和 result.products.is_marketplace_buyout 参数描述。
/v2/finance/realization	更新了方法响应中的 result.rows.delivery_commission.commission、result.rows.delivery_commission.compensation、result.rows.return_commission.commission 和 result.rows.return_commission.compensation 参数描述。
/v4/product/info/limit	更新了方法响应中的 total.limit、daily_create.limit 和 daily_update.limit 参数描述。
/v2/chat/history
/v3/chat/history	更新了方法请求中的 from_message_id 参数描述。
/v1/analytics/product-queries
/v1/analytics/product-queries/details	更新了方法请求中的 page 和 page_size 参数描述。更新了请求示例。
/v3/product/import	将方法请求中的 items.price 参数标记为必需。
更新了方法响应中的 result.task_id 参数描述。
/v2/returns/rfbs/get	更新了方法请求中的 return_id 参数描述。
/v2/fbs/posting/delivering
/v2/fbs/posting/last-mile
/v2/fbs/posting/delivered
/v2/posting/fbs/package-label	更新了方法描述。
/v4/fbs/posting/product/exemplar/status	更新了方法响应中的 status 参数描述。
/v1/actions/products/activate	为方法请求中的 products 参数添加了限制。
—	在 常见错误 部分为 /v3/product/import 方法添加了 price_is_negative 错误描述。更新了 /v4/posting/fbs/ship 方法的 TRANSITION_IS_NOT_POSSIBLE 错误描述。
2025年9月23日
方法	变更
—	已删除模块 推送通知 → Ozon发送的通知 → 商品价格指数的变化。
已删除通知 TYPE_PRICE_INDEX_CHANGED，位置在推送通知 → Ozon发送的通知模块。
2025年9月12日
方法	变更
—	已添加模块API密钥相关信息。
/v1/roles	已将该方法从Beta版迁移至正式版。
2025年9月3日
方法	变更
/v1/conditional-cancellation/get
/v1/conditional-cancellation/list	这些方式已过期，我们已将其从文件中删除。请使用 /v2/conditional-cancellation/list。
/v1/conditional-cancellation/approve	该方式已过期，我们已将其从文件中删除。请使用 /v2/conditional-cancellation/approve。
/v1/conditional-cancellation/reject	该方式已过期，我们已将其从文件中删除。请使用 /v2/conditional-cancellation/reject。
2025年8月27日
方法	变更
—	在方法操作顺序 → FBS仓库操作中，我们详细说明了FBS仓库的操作流程。
/v1/warehouse/fbs/create/drop-off/list
/v1/warehouse/fbs/update/drop-off/list
/v1/warehouse/fbs/create
/v1/warehouse/fbs/update
/v1/warehouse/operation/status
/v2/warehouse/list
/v1/warehouse/fbs/first-mile/update
/v1/warehouse/archive
/v1/warehouse/unarchive	已添加用于FBS仓库操作的方式。
2025年8月21日
方法	变更
/v1/carriage/set-postings	已添加发运组成商品更改方式。
/v1/carriage/cancel	已添加发运删除方式。
/v1/product/action/timer/status	已添加获取已设置计时器状态的方式。
/v1/product/action/timer/update	已为最低价格时效性计时器更新添加方式。
2025年8月15日
方法	变更
—	新增了 Premium方法 板块，并将订阅Premium后可用的方法移至其中。
2025年8月14日
方法	变更
/v3/product/info/list	在方法的响应中新增了参数 items.promotions、items.promotions.is_enabled、items.promotions.type 和 items.sku。
2025年8月5日
方法	变更
/v3/product/info/list	已将参数 items.is_prepayment_allowed 标记为已弃用。
2025年8月1日
方法	变更
/v1/finance/cash-flow-statement/list
/v3/finance/transaction/list
/v3/finance/transaction/totals	更新了方法描述。
2025年7月30日
方法	变更
/v1/analytics/average-delivery-time/summary	添加了用于获取平均配送时间总体分析的 Beta 方法。
2025年7月28日
方法	变更
/v3/chat/list	新增了新版本方法，用于通过指定筛选器获取聊天信息。
/v2/chat/list	该方法将于未来停用。请改用 /v3/chat/list。
/v2/products/stocks	更新了方法描述。
2025年7月23日
方法	变更
/v1/analytics/product-queries
/v1/analytics/product-queries/details
/v1/finance/compensation
/v1/finance/decompensation	已将该方法从Beta版迁移至正式版。
2025年7月22日
方法	变更
/v1/description-category/attribute	更新了方法响应中 result.id 参数的描述。
/v3/posting/fbs/get	更新了方法响应中 result.requirements.products_requiring_change_country 参数的描述。
/v3/posting/fbs/list
/v3/posting/fbs/unfulfilled/list	更新了方法响应中 result.postings.requirements.products_requiring_change_country 和 result.postings.financial_data.products.actions 参数的描述。
/v3/posting/fbs/get	更新了方法响应中 result.financial_data.products.actions 参数的描述。
2025年7月15日
方法	变更
/v1/finance/realization/by-day	已将该方法从Beta版迁移至正式版。
2025年7月14日
方法	变更
/v1/roles	已添加用于使用API密钥获取角色和方式列表的Beta方式。
2025年7月2日
方法	变更
/v3/posting/fbs/get	在方法的响应中新增了参数result.requirements.products_requiring_change_country。
/v3/posting/fbs/unfulfilled/list
/v3/posting/fbs/list	在方法的响应中新增了参数result.postings.requirements.products_requiring_change_country。
2025年7月1日
方法	变更
/v1/product/upload_digital_codes
/v1/product/upload_digital_codes/info	方法已过时， 已从 文档中移除。
—	在 方法操作顺序 → 上传和更新商品 部分，更新了有关方法操作的描述。
2025年6月25日
方法	变更
—	在常见错误模块中，已添加对以下错误的说明：restore limit exceeded 和 total limit exceeded，适用于方法 /v1/product/unarchive。
2025年6月23日
方法	变更
/v3/posting/fbs/get	新了方法响应中result.shipment_date参数的描述。
常见错误	已新增错误 Stock is updated too frequently 的说明，并更新了 /v2/products/stocks 方法中 TOO_MANY_REQUESTS 错误的说明。
2025年6月20日
方法	变更
/v1/barcode/add	新增了为商品绑定条形码的方法。
/v1/barcode/generate	新增了为商品生成条形码的方法。
v1/returns/rfbs/action/set	已将该方法从Beta版迁移至正式版。
—	在 方法操作顺序 → 管理rFBS订单退货申请 部分，更新了有关方法操作的描述。
/v3/posting/fbs/list
/v1/report/list
/v1/delivery-method/list
/v1/description-category/attribute/values/search
/v4/product/info/stocks
/v1/actions/discounts-task/list
/v1/pass/list
/v2/returns/rfbs/list
/v1/returns/company/fbs/info
/v1/analytics/product-queries
/v1/analytics/product-queries/details
/v1/product/info/wrong-volume
/v1/review/comment/list
/v1/review/list	更新了请求示例。
2025年6月19日
方法	变更
/v1/pricing-strategy/product/info	并将参数 result.strategy_competitor_id 标记为已弃用。
/v3/product/info/attributes	该方法已过时。请切换到新版本 /v4/product/info/attributes。
/v1/conditional-cancellation/get	方法即将过时，并将于2025年8月3日停用。请改用 /v2/conditional-cancellation/list。
—	在 管理取消订单 模块，更新了获取rFBS取消申请的方法。
2025年6月18日
方法	变更
/v1/analytics/average-delivery-time	在方法响应中添加了参数 data.metrics.exact_impact_share 和 total.exact_impact_share。
已将参数 data.metrics.impact_share 和 total.impact_share标记为已弃用。
/v1/analytics/average-delivery-time/details	在方法响应中添加了参数 data.metrics.exact_impact_share，并将参数 data.metrics.impact_share 标记为已弃用。
2025年6月11日
方法	变更
/v4/product/info/stocks	在方法的响应中新增了参数items.stocks.warehouse_ids。
2025年6月5日
方法	变更
常见错误	已为所有方法添加了You have reached request rate limit per second的错误描述。
/v1/finance/realization/posting	已将该方法从Beta版迁移至正式版。
/v1/analytics/average-delivery-time	新增用于获取平均配送时间分析的Beta方法。
/v1/analytics/average-delivery-time/details	新增Beta方法，用于获取按集群划分的平均配送时间的详细分析。
/v3/posting/fbs/unfulfilled/list
/v3/posting/fbs/list	在方法请求中添加了参数 with.legal_info 参数的 和 result.postings.legal_info 到方法的响应中。
/v3/posting/fbs/get	在方法请求中添加了参数 with.legal_info 参数的 和 result.legal_info 到方法的响应中。
2025年6月4日
方法	变更
/v2/product/pictures/info	在方法的响应中新增了参数items.errors。
2025年6月3日
方法	变更
/v2/conditional-cancellation/list
/v2/conditional-cancellation/approve
/v2/conditional-cancellation/reject	已将该方法从Beta版迁移至正式版。
/v1/conditional-cancellation/list	方法即将过时，并将于2025年8月3日停用。请切换到新版本 /v2/conditional-cancellation/list。
/v1/conditional-cancellation/approve	方法即将过时，并将于2025年8月3日停用。请切换到新版本 /v2/conditional-cancellation/approve。
/v1/conditional-cancellation/reject	方法即将过时，并将于2025年8月3日停用。请切换到新版本 /v2/conditional-cancellation/reject。
常见错误	已更新方法 /v2/posting/fbs/package-label 的 INVALID_ARGUMENT 错误描述。
2025年5月28日
方法	变更
/v5/product/info/prices	在方法的响应中新增了参数items.price.net_price。
2025年5月27日
方法	变更
/v1/product/import/stocks	该方法已过时，已从文档中删除。请改用 /v2/products/stock。
2025年5月26日
方法	变更
/v3/product/import	在该方法的请求中，已将参数 items.type_id 标记为必填。
2025年5月22日
方法	变更
/v3/posting/fbs/list
/v3/posting/fbs/unfulfilled/list	更新了该方法请求中参数 filter.fbpFilter 的描述。
—	在接口使用规范模块中，已提升接口请求限额 —— 现在每个 Client ID 每秒最多可发起50次请求。此前每秒最多只能发起10次请求。
/v3/product/import	添加字段 items.promotions 在方法请求。
2025年5月15日
方法	变更
/v1/actions/candidates
/v1/actions/products	添加了参数 result.products.alert_max_action_price_failed 和 result.products.alert_max_action_price 到方法的响应中。
2025年5月14
方法	变更
/v1/returns/company/fbs/info	在方法响应中添加了参数 box_count 和 utc_offset。已从方法响应中删除了company_id参数。
2025年5月13日
方法	变更
/v3/chat/history	已将该方法从Beta版迁移至正式版。
/v2/chat/history	方法即将过时，并将于2025年7月13日停用。请切换到新版本 /v3/chat/history。
—	在方法操作顺序→管理聊天 部分，已经列出了用于获取聊天历史的新方法。
/v1/returns/rfbs/action/set	新增用于传递rFBS退货操作的Beta方法。
/v2/returns/rfbs/reject
/v2/returns/rfbs/compensate
/v2/returns/rfbs/verify
/v2/returns/rfbs/receive-return
/v2/returns/rfbs/return-money	这些方法未来将被停用。请切换至 /v1/returns/rfbs/action/set 方法。
2025年5月7日
方法	变更
/v2/conditional-cancellation/approve	新增用于确认 rFBS 订单取消申请的 beta 方法。
/v2/conditional-cancellation/list	新增用于获取 rFBS 订单取消申请列表的 beta 方法。
/v2/conditional-cancellation/reject	新增用于拒绝 rFBS 订单取消申请的 beta 方法。
2025年5月6日
方法	变更
/v3/product/import	已从方法请求中移除参数 items.image_group_id 和 items.premium_price。
/v1/product/import/info	已从方法响应中移除参数 result.items.errors.optional_description_elements。
/v1/product/import-by-sku	已从方法请求中移除参数 items.premium_price。
/v3/posting/fbs/list
/v3/posting/fbs/unfulfilled/list	已从方法响应中移除参数 result.postings.financial_data.products.client_price，result.postings.financial_data.products.picking 和 result.postings.products.mandatory_mark。
/v3/posting/fbs/get
/v2/posting/fbs/get-by-barcode	已从方法响应中移除参数 result.financial_data.products.client_price，result.financial_data.products.picking 和 result.products.mandatory_mark。
/v1/finance/realization	该方法已过时，已从文档中删除。 请改用 /v2/finance/realization。
/v1/product/import/stocks	方法将于2025年5月27日停用。请改用 /v2/products/stocks。
2025年5月5日
方法	变更
/v1/finance/compensation	新增用于获取赔偿报告的 Beta 方法。
/v1/finance/decompensation	新增用于获取赔偿返还报告的 Beta 方法。
/v1/report/info	更新了 report_type 参数在方法响应中的描述。
/v1/report/list	更新了 report_type 参数在方法请求和响应中的描述。
2025年4月25日
方法	变更
/v1/finance/realization/posting	我们已添加用于获取按订单细分的商品销售报告的Beta方式。
2025年4月22日
方法	变更
/v1/finance/realization/by-day	已新增用于获取每日商品销售报告的Beta方法。
2025年4月11日
方法	变更
/v3/posting/fbs/get
/v2/posting/fbs/get-by-barcode	已从方法响应中删除了过时的 result.financial_data.posting_services 和 result.financial_data.products.item_services 参数。
/v3/posting/fbs/list
/v3/posting/fbs/unfulfilled/list	已从方法响应中删除了过时的 result.postings.financial_data.posting_services 和 result.postings.financial_data.products.item_services 参数。
2025年3月26日
方法	变更
/v1/product/info/wrong-volume	我们已为获取体积重量特征不正确的商品的列表添加了Beta方法。
2025年3月20日
方法	变更
/v1/analytics/product-queries/details	更新了方法请求中 limit_by_sku，page 和 page_size 参数的描述。
2025年3月19日
方法	变更
/v1/product/import/info	更新了方法描述。
在方法响应的 result.items.status 参数中，新增可能的值 skipped。
/v1/description-category/attribute	在方法响应中添加了参数 result.complex_is_collection。
2025年3月14日
方法	变更
/v1/analytics/product-queries/details	我们添加了获得特定商品查询数据的方法。
2025年3月13日
方法	变更
/v1/actions/candidates
/v1/actions/products	我们已将 offset 参数标记为已弃用，并添加了 last_id分页参数。
/v1/analytics/product-queries	添加了获取商品搜索查询数据的 Beta 方法。
2025年3月11日
方法	变更
/v3/chat/history	添加了新版本的聊天记录查看方法。
/v1/actions/hotsales/activate
/v1/actions/hotsales/deactivate
/v1/actions/hotsales/list
/v1/actions/hotsales/products	方法已过时，已从文档中移除。
2025年3月10日
方法	变更
/v2/product/info	该方法已过期，我们已将其从文件中删除。请使用 /v3/product/info/list。
2025年3月7日
方法	变更
/v5/product/info/prices	已将该方法从 Beta 版迁移至正式版。
2025年3月3日
方法	变更
/v3/posting/fbs/get	在方法响应中：
• 已添加 result.optional.products_with_possible_mandatory_mark 参数，
• 已将 result.products.mandatory_mark 参数标记为过时参数。
/v3/posting/fbs/list
/v3/posting/fbs/unfulfilled/list	在方法响应中：
• 已添加 result.postings.optional.products_with_possible_mandatory_mark 参数，
• 已将 result.postings.products.mandatory_mark 参数标记为过时参数。
/v3/posting/fbs/get
/v3/posting/fbs/list
/v3/posting/fbs/unfulfilled/list	您可以通过 /v3/posting/fbs/get 方法（请在方法请求指出 with.product_exemplars: true）或 /v6/fbs/posting/product/exemplar/create-or-get 方法来获取标志代码的含义。
2025年2月27日
方法	变更
/v1/warehouse/list
/v1/actions	更新了方法描述。
/v3/posting/fbs/get	在方法响应中添加了参数 result.previous_substatus。
/v3/finance/transaction/list	更新了方法响应中result.operations.posting.delivery_schema参数的描述 。
2025年2月26日
方法	变更
/v3/posting/fbs/unfulfilled/list
/v3/posting/fbs/list	更新了方法响应中 result.postings.analytics_data.city 参数的描述。
/v3/posting/fbs/get	更新了方法响应中 result.analytics_data.city参数的描述。
2025年2月24日
方法	变更
/v1/product/import/prices	在方法请求中添加了参数 prices.net_price，用于指定商品的成本价。
2025年2月21日
方法	变更
/v3/product/list	新增获取所有商品列表的方法。
2025年2月18日
方法	变更
/v4/product/info/prices	该方式已过期，我们已将其从文件中删除。
/v3/returns/company/fbs	该方式已过期，我们已将其从文件中删除。请使用 /v1/returns/list.
/v1/report/returns/create	该方式已过期，我们已将其从文件中删除。
2025年2月17日
方法	变更
/v2/product/info/list	该方式已过期，我们已将其从文件中删除。请使用 /v3/product/info/list。
/v6/fbs/posting/product/exemplar/set
/v6/fbs/posting/product/exemplar/create-or-get
/v5/fbs/posting/product/exemplar/status
/v5/fbs/posting/product/exemplar/validate
/v1/fbs/posting/product/exemplar/update	新增 Beta 方法用于管理标志代码。
2025年2月11日
方法	变更
/v3/product/info/list	已将此方法从测试版移至正式版。
/v2/product/list	该方式已过期，我们已将其从文件中删除。
2025年2月10日
方法	变更
/v4/product/info/stocks	已将此方法从测试版移至正式版。
/v3/product/info/stocks	该方式已过期，我们已将其从文件中删除。
/v1/product/pictures/info	该方式已过期，我们已将其从文件中删除。请使用 /v2/product/pictures/info。
2025年2月6日
方法	变更
/v2/posting/fbs/awaiting-delivery	更新了该方法请求中参数 posting_number 的描述。
2025年1月16日
方法	变更
/v1/review/comment/create
/v1/review/comment/delete
/v1/review/comment/list
/v1/review/change-status
/v1/review/count
/v1/review/info
/v1/review/list	已添加管理评价的测试方法。