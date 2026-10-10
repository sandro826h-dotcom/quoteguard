# Step 0 summary (step0-live)

Status: completed; completed 17/17; estimated cost {"USD": 0.000465, "CNY": 0.027749}

| run_key | returned model | HTTP | latency ms | prompt tok | completion tok | finish | reasoning | retries | text |
|---|---|---|---|---|---|---|---|---|---|
| step0/think/ds_flash_think/arith/probe/c0/r1 | deepseek-flash | 200 | 976.76 | 39 | 22 | stop | True | 0 | 42 |
| step0/think/ds_flash/arith/probe/c0/r1 | deepseek-flash | 200 | 706.91 | 13 | 1 | stop | False | 0 | 42 |
| step0/think/ds_flash/arith/probe/c0/r2 | deepseek-flash | 200 | 807.34 | 13 | 1 | stop | False | 0 | 42 |
| step0/think/ds_pro/arith/probe/c0/r1 | deepseek-v4-pro | 200 | 1494.62 | 13 | 1 | stop | False | 0 | 42 |
| step0/think/glm/arith/probe/c0/r1 | zai-org/GLM-5.3 | 200 | 2656.74 | 21 | 45 | stop | True | 0 | 42 |
| step0/think/kimi/arith/probe/c0/r1 | Pro/moonshotai/Kimi-K2.6 | 200 | 1098.24 | 18 | 1 | stop | False | 0 | 42 |
| step0/think/kimi/arith/probe/c0/r2 | Pro/moonshotai/Kimi-K2.6 | 200 | 1143.53 | 18 | 1 | stop | False | 0 | 42 |
| step0/think/kimi/arith/probe/c0/r3 | Pro/moonshotai/Kimi-K2.6 | 200 | 1178.78 | 18 | 1 | stop | False | 0 | 42 |
| step0/think/kimi/arith/probe/c0/r4 | Pro/moonshotai/Kimi-K2.6 | 200 | 1076.11 | 18 | 1 | stop | False | 0 | 42 |
| step0/think/kimi/arith/probe/c0/r5 | Pro/moonshotai/Kimi-K2.6 | 200 | 914.13 | 18 | 1 | stop | False | 0 | 42 |
| step0/think/qwen_max/arith/probe/c0/r1 | qwen3.8-max-0902 | 200 | 1819.28 | 23 | 2 | stop | False | 0 | 42 |
| step0/think/qwen_plus/arith/probe/c0/r1 | qwen-plus | 200 | 850.28 | 19 | 2 | stop | False | 0 | 42 |
| step0/think/qwen_plus/arith/probe/c0/r2 | qwen-plus | 200 | 825.11 | 19 | 2 | stop | False | 0 | 42 |
| step0/vision/ds_flash/table/probe/c0/r1 | deepseek-flash | 200 | 1651.61 | 615 | 71 | stop | False | 0 | 美国专线 运价表 重量档 单价（元/公斤） 1-45 公斤 28.50 46-100 公斤 24.00 101 公斤以上 21.50 附加费 燃油附加费：运费的 |
| step0/vision/glm_v/table/probe/c0/r1 | zai-org/GLM-4.5V | 200 | 2709.93 | 1267 | 71 | stop | False | 0 | </begin_of_box/>美国专线 运价表 重量档	单价（元/公斤） 1-45 公斤	28.50 46-100 公斤	24.00 101 公斤以上	21. |
| step0/vision/kimi/table/probe/c0/r1 | Pro/moonshotai/Kimi-K2.6 | 200 | 4371.65 | 1269 | 87 | stop | False | 0 | 美国专线 运价表  / 重量档 / 单价（元/公斤） / /--------/----------------/ / 1-45 公斤 / 28.50 / / 4 |
| step0/vision/qwen_vl/table/probe/c0/r1 | qwen3-vl-plus | 200 | 5696.3 | 970 | 118 | stop | True | 0 | 以下为图中表格的逐行文字转写：  美国专线 运价表   重量档　　　　　　　　　单价（元/公斤）   1-45 公斤　　　　　　　　28.50   46-100 |

## Seed repeatability
- ds_flash: identical=True (first='42', second='42')
- qwen_plus: identical=True (first='42', second='42')

## Kimi latency
- n=5, median=1098.24 ms, retries=0
