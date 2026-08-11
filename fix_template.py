#!/usr/bin/env python3
"""
修复 TemplateResponse 调用方式
新版 Starlette 需要使用 request= 和 context= 参数
"""

import re

file_path = "ui/web_app.py"

with open(file_path, 'r', encoding='utf-8') as f:
    content = f.read()

# 修复 TemplateResponse 调用
# 从: self.templates.TemplateResponse("template.html", {"request": request, ...})
# 到: self.templates.TemplateResponse("template.html", request=request, context={...})

def fix_template_response(match):
    template_name = match.group(1)
    dict_content = match.group(2)

    # 提取 request 和其他参数
    # 移除 "request": request,
    dict_content_clean = re.sub(r'"request":\s*request,?\s*', '', dict_content)

    # 如果字典为空，返回空的 context
    if dict_content_clean.strip() in ['', '{}']:
        return f'self.templates.TemplateResponse({template_name}, request=request)'
    else:
        # 确保字典格式正确
        if not dict_content_clean.strip().startswith('{'):
            dict_content_clean = '{' + dict_content_clean
        if not dict_content_clean.strip().endswith('}'):
            dict_content_clean = dict_content_clean + '}'

        return f'self.templates.TemplateResponse({template_name}, request=request, context={dict_content_clean})'

# 匹配模式
pattern = r'self\.templates\.TemplateResponse\(\s*("[\w\.]+"),\s*(\{[^}]*"request":\s*request[^}]*\})\s*\)'

content_fixed = re.sub(pattern, fix_template_response, content, flags=re.DOTALL)

with open(file_path, 'w', encoding='utf-8') as f:
    f.write(content_fixed)

print("✅ 已修复 TemplateResponse 调用")
