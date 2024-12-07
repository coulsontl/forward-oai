import os
import json
import re
import time
from aiohttp import ClientSession, web
from dotenv import load_dotenv
import logging
import threading
import chardet

# 加载环境变量
load_dotenv()

# 配置日志
logging.basicConfig(level=logging.INFO, format='%(asctime)s - %(levelname)s - %(message)s')

# 获取环境变量值，支持大小写不敏感，空值返回默认值。
def get_env_value(key, default=None):
    value = os.getenv(key) or os.getenv(key.lower()) or os.getenv(key.upper())
    return default if value in [None, ''] else value

# 从环境变量读取代理设置（支持大小写）
http_proxy = get_env_value('HTTP_PROXY')
https_proxy = get_env_value('HTTPS_PROXY')

# 尝试读取 config.json 文件
try:
    with open('config.json', 'r') as config_file:
        app_config = json.load(config_file)
except FileNotFoundError:
    app_config = {}

async def fetch(req, is_chat):
    if req.method == "OPTIONS":
        return create_options_response()

    try:
        body = None
        model = '*'
        if req.can_read_body:
            content_type = req.content_type.lower()
            if content_type.startswith('application/json'):
                body = await req.json()
                model = body.get("model", '*')
            else:
                body = await req.text()
        token = get_request_token(req)
        config = app_config.get(token, app_config.get("*", {}))
        # logging.info(f"config: {config}")

        if is_chat:
            url = prepare_chat_url(body, config)
        else:
            url = prepare_other_url(req, config)
        if url is None or url == "":
            raise ValueError("config.json中没有配置模型对应的URL")
        
        data = prepare_data(body, config)
        headers = prepare_headers(req, model, config, is_chat)
        response = await post_request(url, data, headers, req)
        return response
    except Exception as e:
        logging.error(f"Error processing request: {str(e)}")
        return web.Response(text=str(e), status=500)

def get_request_token(req):
    auth_header = req.headers.get('authorization') or req.headers.get('Authorization')
    if auth_header:
        parts = auth_header.split()
        # 检查是否有两个部分，并且第一个部分是 'Bearer'（不区分大小写）
        if len(parts) == 2 and parts[0].lower() == 'bearer':
            token = parts[1]
        else:
            print("Invalid authorization header format.")
    else:
        print("Authorization header is missing.")
    return token

def create_options_response():
    return web.Response(body="", headers={
        'Access-Control-Allow-Origin': '*',
        'Access-Control-Allow-Headers': '*'
    }, status=204)

def prepare_chat_url(body, config):
    model = body.get("model")
    logging.info(f"model: {model}")
    url = config.get(model, config.get("*", {})).get("chat-url")
    logging.info(f"url: {url}")
    return url

def prepare_other_url(req, config):
    domain = config.get('*', {}).get("domain")
    logging.info(f"url: {domain + req.path}")
    return domain + req.path

def prepare_data(body, config):
    # logging.info(f"prepare data with body: {body}")
    return body

def prepare_headers(req, model, config, is_chat):
    headers = dict(req.headers)
    headers.pop('Host', None)
    headers.pop('Content-Length', None)
    # headers = {'Content-Type': 'application/json; charset=utf-8', 'Accept': '*/*', 'Accept-Encoding': 'gzip, deflate, br, zstd', 'User-Agent': 'Mozilla/5.0 (Macintosh; Intel Mac OS X 10_15_7) AppleWebKit/537.36 (KHTML, like Gecko) Chrome/123.0.0.0 Safari/537.36'}
    key = config.get(model, {}).get("key") or config.get('*', {}).get("key")
    if key is None or key == "":
        authorization = req.headers.get('authorization')
    else:
        if is_chat == False and key.endswith("-ca"):
            authorization = f"Bearer {key[:-3]}"
        else:
            authorization = f"Bearer {key}"
    headers["Authorization"] = authorization
    logging.info(f"headers: {headers}")

    return headers

async def post_request(url, data, headers, req):
    async with ClientSession(trust_env=True) as session:
        return await send_request(session, url, data, headers, req)

async def send_request(session, url, data, headers, req):
    method = req.method.lower()
    request_method = getattr(session, method, None)

    if request_method is None:
        logging.error(f"Unsupported HTTP method: {req.method}")
        return None

    async with request_method(url, json=data, headers=headers, proxy=http_proxy or https_proxy) as resp:
        if resp.status != 200:
            response_text = await resp.text()
            logging.error(f"Error from API: Status: {resp.status}, Body: {response_text}")
            return resp
        return await handle_response(data, resp, req)

async def handle_response(data, resp, req):
    if data == None or not data.get("stream"):
        body = await resp.read()
        response = web.Response(
            body=body,
            content_type=resp.content_type,
            status=resp.status,
            headers={
                'Access-Control-Allow-Origin': '*',
                'Access-Control-Allow-Headers': '*'
            }
        )
        return response
    else:
        return await stream_response(resp, req)

async def stream_response(resp, req):
    writer = web.StreamResponse()
    writer.headers['Access-Control-Allow-Origin'] = '*'
    writer.headers['Access-Control-Allow-Headers'] = '*'
    writer.headers['Content-Type'] = 'text/event-stream; charset=UTF-8'
    await writer.prepare(req)
    
    async for chunk in resp.content.iter_any():
        await writer.write(chunk)

    await writer.write_eof()
    return writer

async def onChatRequest(request):
    return await fetch(request, True)

async def onOtherRequest(request):
    return await fetch(request, False)

app = web.Application()
app.router.add_route("*", "/v1/chat/completions", onChatRequest)
app.router.add_route("*", "/{tail:.*}", onOtherRequest)

if __name__ == '__main__':
    port = int(get_env_value('SERVER_PORT', 3030))
    web.run_app(app, host='0.0.0.0', port=port)