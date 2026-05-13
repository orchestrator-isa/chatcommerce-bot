# scripts/fix_routes_order.py
import re

with open("main.py", "r", encoding="utf-8") as f:
    content = f.read()

# Extraer endpoint admin
admin_pattern = r'(@app\.post\("/admin/refresh-schema"\).*?^(?=@app\.))'
admin_match = re.search(admin_pattern, content, re.DOTALL | re.MULTILINE)

if admin_match:
    admin_block = admin_match.group(1)
    # Remover del lugar actual
    content = content.replace(admin_block, "")
    # Insertar antes de catch-all
    catch_all_pattern = r'(@app\.(get|post)\("/{path:path}"\))'
    content = re.sub(catch_all_pattern, admin_block + "\n\n\\1", content, count=1)
    
    with open("main.py", "w", encoding="utf-8") as f:
        f.write(content)
    print("✅ Rutas reordenadas")# scripts/fix_routes_order.py
import re

with open("main.py", "r", encoding="utf-8") as f:
    content = f.read()

# Extraer endpoint admin
admin_pattern = r'(@app\.post\("/admin/refresh-schema"\).*?^(?=@app\.))'
admin_match = re.search(admin_pattern, content, re.DOTALL | re.MULTILINE)

if admin_match:
    admin_block = admin_match.group(1)
    # Remover del lugar actual
    content = content.replace(admin_block, "")
    # Insertar antes de catch-all
    catch_all_pattern = r'(@app\.(get|post)\("/{path:path}"\))'
    content = re.sub(catch_all_pattern, admin_block + "\n\n\\1", content, count=1)
    
    with open("main.py", "w", encoding="utf-8") as f:
        f.write(content)
    print("✅ Rutas reordenadas")
