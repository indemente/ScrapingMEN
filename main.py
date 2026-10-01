import asyncio
import json
import os
import random
import re
import smtplib
import sys
from dataclasses import dataclass, asdict
from datetime import datetime
from email.mime.text import MIMEText
from email.mime.multipart import MIMEMultipart
from playwright.async_api import async_playwright


# ============================================================
# UTILIDADES
# ============================================================

def remove_accents(text):
    import unicodedata
    return ''.join(c for c in unicodedata.normalize('NFD', text) if unicodedata.category(c) != 'Mn')


def tiempo_humano(min_seg=1.0, max_seg=3.0):
    return random.uniform(min_seg, max_seg)


# ============================================================
# MODELO DE DATOS
# ============================================================

@dataclass
class Vacante:
    cargo: str = ""
    area: str = ""
    secretaria: str = ""
    zona: str = ""
    departamento: str = ""
    municipio: str = ""
    tipo: str = ""
    postulados: str = ""
    cierre: str = ""
    establecimiento: str = ""
    sede: str = ""
    barrio: str = ""
    direccion: str = ""
    calendario: str = ""
    fuente: str = "SistemaMaestro"


# ============================================================
# EXTRACCIÓN
# ============================================================

async def extraer_detalle_vacante(page, vacante_idx: int, total_vacantes: int) -> dict:
    """Extrae los detalles de una vacante abriendo el modal Ver detalle"""
    detalle = {"establecimiento": "", "sede": "", "barrio": "", "direccion": "", "calendario": ""}

    try:
        vacante_divs = await page.query_selector_all("div.vacante")

        if vacante_idx >= len(vacante_divs):
            return detalle

        div = vacante_divs[vacante_idx]

        all_links = await div.query_selector_all("a")
        ver_detalle = None
        for link in all_links:
            text = await link.inner_text()
            if text and 'Ver detalle' in text:
                ver_detalle = link
                break

        if not ver_detalle:
            return detalle

        await ver_detalle.scroll_into_view_if_needed()
        await asyncio.sleep(0.5)

        await ver_detalle.click()

        await asyncio.sleep(10)

        page_text = await page.evaluate('document.body.innerText')

        if 'Establecimiento educativo:' in page_text or 'Establecimiento:' in page_text:
            lines = page_text.split('\n')
            for i, line in enumerate(lines):
                line = line.strip()
                if not line:
                    continue

                if 'Establecimiento educativo:' in line:
                    detalle['establecimiento'] = line.split(':')[-1].strip()
                elif 'Establecimiento:' in line:
                    detalle['establecimiento'] = line.split(':')[-1].strip()

                if line.startswith('Sede:') or 'Sede:' in line:
                    detalle['sede'] = line.split(':')[-1].strip()

                if 'Barrio:' in line:
                    detalle['barrio'] = line.split(':')[-1].strip()

                if 'Dirección:' in line or 'Direcci' in line and ':' in line:
                    detalle['direccion'] = line.split(':')[-1].strip()

                if 'Calendario' in line and ':' in line:
                    detalle['calendario'] = line.split(':')[-1].strip()

        await page.keyboard.press("Escape")
        await asyncio.sleep(1)
    except Exception as e:
        pass

    return detalle


async def extraer_vacantes(page, browser) -> list:
    """Extrae todas las vacantes de la pagina actual"""
    vacantes = []
    vacante_divs = await page.query_selector_all("div.vacante")

    for vac_idx, div in enumerate(vacante_divs):
        try:
            data = await div.evaluate('''(el) => {
                const labels = el.querySelectorAll('label');
                const d = {};
                labels.forEach(l => {
                    const t = l.innerText || "";
                    if (t.startsWith('Cargo ')) d.cargo = t.replace('Cargo ', '');
                    else if (t.startsWith('Postulados:')) d.postulados = t.replace('Postulados:', '').trim();
                    else if (t.startsWith('Tipo Priorización:')) d.tipo = t.replace('Tipo Priorización:', '').trim();
                    else if (t.startsWith('Cierre vacante:')) d.cierre = t.replace('Cierre vacante:', '').trim();
                    else if (t.startsWith('Área:')) d.area = t.replace('Área:', '').trim();
                    else if (t.startsWith('Secretaría de Educación:')) d.secretaria = t.replace('Secretaría de Educación:', '').trim();
                    else if (t.startsWith('Zona:')) d.zona = t.replace('Zona:', '').trim();
                    else if (t.startsWith('Departamento:')) d.departamento = t.replace('Departamento:', '').trim();
                    else if (t.startsWith('Municipio:')) d.municipio = t.replace('Municipio:', '').trim();
                });
                return d;
            }''')

            detalle = await extraer_detalle_vacante(page, vac_idx, len(vacante_divs))

            vacantes.append(Vacante(
                cargo=data.get('cargo', ''),
                area=data.get('area', ''),
                secretaria=data.get('secretaria', ''),
                zona=data.get('zona', ''),
                departamento=data.get('departamento', ''),
                municipio=data.get('municipio', ''),
                tipo=data.get('tipo', ''),
                postulados=data.get('postulados', ''),
                cierre=data.get('cierre', ''),
                establecimiento=detalle.get('establecimiento', ''),
                sede=detalle.get('sede', ''),
                barrio=detalle.get('barrio', ''),
                direccion=detalle.get('direccion', ''),
                calendario=detalle.get('calendario', '')
            ))
        except:
            pass
    return vacantes


async def esperar_vacantes_carguen(page) -> int:
    for _ in range(8):
        await asyncio.sleep(tiempo_humano(0.8, 1.2))
        count = await page.locator("div.vacante").count()
        if count > 0:
            await asyncio.sleep(tiempo_humano(1, 1.5))
            return await page.locator("div.vacante").count()
    return await page.locator("div.vacante").count()


async def hay_next_habilitado(page) -> bool:
    return await page.evaluate('''() => {
        const nextBtn = document.querySelector('a.ui-paginator-next');
        return nextBtn && !nextBtn.classList.contains('ui-state-disabled');
    }''')


async def hacer_click_next(page) -> bool:
    try:
        await page.evaluate('''() => {
            const nextBtn = document.querySelector('a.ui-paginator-next');
            if (nextBtn && !nextBtn.classList.contains('ui-state-disabled')) nextBtn.click();
        }''')
        await asyncio.sleep(tiempo_humano(0.3, 0.8))
        return True
    except:
        return False


# ============================================================
# BÚSQUEDA POR ÁREA (una sola área)
# ============================================================

async def buscar_por_area(nombre_area: str) -> dict:
    async with async_playwright() as p:
        browser = await p.chromium.launch(
            headless=True,
            args=[
                '--disable-blink-features=AutomationControlled',
                '--no-sandbox',
                '--disable-setuid-sandbox',
                '--disable-dev-shm-usage',
                '--disable-web-security',
                '--disable-features=IsolateOrigins,site-per-process',
                '--allow-running-insecure-content',
                '--disable-gpu'
            ]
        )

        context = await browser.new_context(
            viewport={'width': 1920, 'height': 1080},
            user_agent='Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36 (KHTML, like Gecko) Chrome/120.0.0.0 Safari/537.36'
        )

        page = await context.new_page()

        print(f"\n>>> Buscando: {nombre_area}")
        print(">>> Cargando página...")

        try:
            await page.goto(
                "https://sistemamaestro.mineducacion.gov.co/SistemaMaestro/busquedaVacantes.xhtml",
                wait_until="networkidle"
            )
            await asyncio.sleep(tiempo_humano(4, 6))

            if "blocked" in (await page.title()).lower():
                print("ERROR: Página bloqueada")
                await browser.close()
                return {"error": "Página bloqueada", "area": nombre_area}

            select_area = page.locator("#form-busqueda\\:idInputArea")

            try:
                await select_area.wait_for(state="visible", timeout=60000)
            except:
                print("ERROR: No se cargó dropdown Area - reintentando...")
                await asyncio.sleep(5)
                try:
                    await page.reload()
                    await asyncio.sleep(5)
                    await select_area.wait_for(state="visible", timeout=60000)
                except:
                    print("ERROR: No se pudo cargar el dropdown Area")
                    await browser.close()
                    return {"error": "No se cargó el dropdown", "area": nombre_area}

            await asyncio.sleep(tiempo_humano(0.5, 1))
            await select_area.click()
            await asyncio.sleep(tiempo_humano(1, 2))

            opciones = await page.evaluate('''() => {
                const panel = document.getElementById("form-busqueda:idInputArea_panel");
                if (!panel) return [];
                const items = panel.querySelectorAll(".ui-selectonemenu-item");
                const resultado = [];
                items.forEach((item, idx) => {
                    if (item.textContent.trim()) {
                        resultado.push({idx: idx, texto: item.textContent.trim()});
                    }
                });
                return resultado;
            }''')

            areas = [(str(i["idx"]), i["texto"]) for i in opciones if "/" not in i["texto"] and i["texto"] != "Área"]

            area_encontrada = None
            nombre_area_norm = remove_accents(nombre_area.lower().strip())

            # 1) Coincidencia EXACTA primero
            for idx, nombre in areas:
                if remove_accents(nombre.lower().strip()) == nombre_area_norm:
                    area_encontrada = (idx, nombre)
                    break

            # 2) Fallback: coincidencia PARCIAL (contiene)
            if not area_encontrada:
                for idx, nombre in areas:
                    if nombre_area_norm in remove_accents(nombre.lower()):
                        area_encontrada = (idx, nombre)
                        break

            if not area_encontrada:
                print(f"!!! Área '{nombre_area}' no encontrada")
                print(f"!!! Áreas disponibles ({len(areas)}):")
                for a in areas:
                    print(f"      - {a[1]}")
                await browser.close()
                return {
                    "error": f"Área '{nombre_area}' no encontrada",
                    "area": nombre_area,
                    "areas_disponibles": [a[1] for a in areas]
                }

            idx, nombre = area_encontrada
            print(f">>> Área encontrada: {nombre}")

            await select_area.click()
            await asyncio.sleep(tiempo_humano(0.5, 1))

            await page.evaluate(f'''(itemIdx) => {{
                const panel = document.getElementById("form-busqueda:idInputArea_panel");
                const items = panel.querySelectorAll(".ui-selectonemenu-item");
                if (items[itemIdx]) items[itemIdx].click();
            }}''', int(idx))
            await asyncio.sleep(tiempo_humano(1, 2))

            await page.mouse.click(10, 10)
            await asyncio.sleep(tiempo_humano(0.5, 1))

            await page.evaluate("document.getElementById('form-busqueda:busqueda').click()")
            await asyncio.sleep(tiempo_humano(3, 5))

            count = await page.locator("div.vacante").count()

            if count == 0:
                print(f">>> Sin vacantes para el área: {nombre}")
                await browser.close()
                return {
                    "area": nombre,
                    "total": 0,
                    "vacantes": []
                }

            print(f">>> [{count} vacantes en página 1]")

            vacantes = await extraer_vacantes(page, browser)
            total_area = len(vacantes)
            print(f">>> +{len(vacantes)} vacantes extraídas")

            if total_area >= 6 and await hay_next_habilitado(page):
                await asyncio.sleep(tiempo_humano(1, 2))
                pagina = 1
                while True:
                    if not await hay_next_habilitado(page):
                        break
                    await hacer_click_next(page)
                    pagina += 1
                    await asyncio.sleep(tiempo_humano(2, 4))
                    await esperar_vacantes_carguen(page)
                    vacantes_pag = await extraer_vacantes(page, browser)
                    if vacantes_pag:
                        total_area += len(vacantes_pag)
                        vacantes.extend(vacantes_pag)
                        print(f">>> Página {pagina}: +{len(vacantes_pag)}")
                    else:
                        break

            print(f">>> Total área '{nombre}': {total_area}")
            await browser.close()

            return {
                "area": nombre,
                "total": len(vacantes),
                "fecha_ejecucion": datetime.now().strftime('%Y-%m-%d %H:%M:%S'),
                "vacantes": [asdict(v) for v in vacantes]
            }

        except Exception as e:
            print(f"ERROR en buscar_por_area({nombre_area}): {e}")
            try:
                await browser.close()
            except:
                pass
            return {"error": str(e), "area": nombre_area}


# ============================================================
# BÚSQUEDA POR MÚLTIPLES ÁREAS
# ============================================================

async def buscar_multiples_areas(areas: list) -> list:
    """Ejecuta buscar_por_area() para cada área de la lista."""
    resultados = []
    for i, area in enumerate(areas, 1):
        area = area.strip()
        if not area:
            continue
        print(f"\n{'=' * 60}")
        print(f"[{i}/{len(areas)}] Procesando área: {area}")
        print(f"{'=' * 60}")
        try:
            resultado = await buscar_por_area(area)
        except Exception as e:
            print(f"Error inesperado con '{area}': {e}")
            resultado = {"error": str(e), "area": area}
        resultados.append(resultado)
        if i < len(areas):
            pausa = tiempo_humano(3, 6)
            print(f"\n[... esperando {pausa:.1f}s antes de la siguiente área ...]")
            await asyncio.sleep(pausa)
    return resultados


# ============================================================
# ENVÍO DE CORREO
# ============================================================

def enviar_email(area: str, vacantes: list) -> None:
    """Envía un correo con las vacantes encontradas usando SMTP de Gmail."""
    smtp_user = os.getenv("SMTP_USER")
    smtp_pass = os.getenv("SMTP_PASS")
    email_to = os.getenv("EMAIL_TO", smtp_user)

    if not smtp_user or not smtp_pass:
        print("[email] Faltan credenciales SMTP_USER o SMTP_PASS. No se envía correo.")
        return

    filas = ""
    for v in vacantes:
        filas += f"""
        <tr>
          <td style="padding:6px;border:1px solid #ccc;">{v.get('cargo','')}</td>
          <td style="padding:6px;border:1px solid #ccc;">{v.get('municipio','')}</td>
          <td style="padding:6px;border:1px solid #ccc;">{v.get('secretaria','')}</td>
          <td style="padding:6px;border:1px solid #ccc;">{v.get('establecimiento','')}</td>
          <td style="padding:6px;border:1px solid #ccc;">{v.get('sede','')}</td>
          <td style="padding:6px;border:1px solid #ccc;">{v.get('cierre','')}</td>
        </tr>"""

    html = f"""
    <html><body style="font-family:Arial,sans-serif;">
    <h2 style="color:#1a4d80;">Nuevas vacantes — Área: {area}</h2>
    <p>Se encontraron <strong>{len(vacantes)}</strong> vacante(s) nueva(s).</p>
    <table style="border-collapse:collapse;font-size:13px;">
      <thead>
        <tr style="background:#f0f0f0;">
          <th style="padding:6px;border:1px solid #ccc;">Cargo</th>
          <th style="padding:6px;border:1px solid #ccc;">Municipio</th>
          <th style="padding:6px;border:1px solid #ccc;">Secretaría</th>
          <th style="padding:6px;border:1px solid #ccc;">Establecimiento</th>
          <th style="padding:6px;border:1px solid #ccc;">Sede</th>
          <th style="padding:6px;border:1px solid #ccc;">Cierre</th>
        </tr>
      </thead>
      <tbody>{filas}</tbody>
    </table>
    <p style="color:#888;font-size:12px;">Generado automáticamente por Scraper Maestro — {datetime.now().strftime('%Y-%m-%d %H:%M:%S')}</p>
    </body></html>
    """

    msg = MIMEMultipart("alternative")
    msg["Subject"] = f"[Sistema Maestro] {len(vacantes)} vacantes nuevas en {area}"
    msg["From"] = smtp_user
    msg["To"] = email_to
    msg.attach(MIMEText(html, "html", "utf-8"))

    try:
        with smtplib.SMTP("smtp.gmail.com", 587, timeout=30) as server:
            server.starttls()
            server.login(smtp_user, smtp_pass)
            server.sendmail(smtp_user, [email_to], msg.as_string())
        print(f"[email] ✅ Correo enviado a {email_to}")
    except Exception as e:
        print(f"[email] ❌ Error al enviar correo: {e}")


# ============================================================
# MAIN
# ============================================================

async def main():
    """
    Uso:
      python scraper_area_chat.py "Tecnología e informática,Primaria"
      python scraper_area_chat.py "Tecnología e informática" "Primaria"

    Sin argumentos, lee la variable de entorno AREAS_BUSQUEDA
    (formato: "Área1,Área2,Área3").
    """
    # 1. Determinar áreas
    if len(sys.argv) >= 2:
        areas_raw = ",".join(sys.argv[1:])
    else:
        areas_raw = os.getenv("AREAS_BUSQUEDA", "Tecnología e informática,Primaria")

    areas = [a.strip() for a in areas_raw.split(",") if a.strip()]

    if not areas:
        print("ERROR: No se especificó ninguna área.")
        print('Uso: python scraper_area_chat.py "Tecnología e informática,Primaria"')
        return

    print("=" * 60)
    print(f"SCRAPER SISTEMA MAESTRO — {len(areas)} área(s) a procesar")
    print("=" * 60)
    for a in areas:
        print(f"  • {a}")
    print()

    # 2. Ejecutar scraping
    resultados = await buscar_multiples_areas(areas)

    # 3. Resumen + envío de correos
    print("\n" + "=" * 60)
    print("RESUMEN FINAL")
    print("=" * 60)

    resumen = {}
    for res in resultados:
        area = res.get("area", "?")
        if "error" in res:
            print(f"  ❌ {area}: ERROR — {res['error']}")
            resumen[area] = {"total": 0, "error": res["error"]}
            continue
        total = res.get("total", 0)
        print(f"  ✅ {area}: {total} vacantes")
        resumen[area] = {"total": total}

        if total > 0:
            enviar_email(area, res.get("vacantes", []))

    # 4. JSON completo
    print("\nJSON_OUTPUT_START")
    print(json.dumps({
        "fecha_ejecucion": datetime.now().strftime('%Y-%m-%d %H:%M:%S'),
        "areas": [r.get("area") for r in resultados],
        "resumen": resumen,
        "resultados": resultados
    }, ensure_ascii=False, indent=2))
    print("JSON_OUTPUT_END")


if __name__ == "__main__":
    asyncio.run(main())
