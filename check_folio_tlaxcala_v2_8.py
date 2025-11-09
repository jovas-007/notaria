#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""
check_folio_tlaxcala_v2_8.py
(Ver descripción en el encabezado de cambios multi-modo y lógica HIT estricta.)
"""
import csv
import sys
import time
import argparse
import string
from pathlib import Path

from selenium import webdriver
from selenium.webdriver.common.by import By
from selenium.webdriver.support.ui import WebDriverWait
from selenium.webdriver.support import expected_conditions as EC
from selenium.common.exceptions import (
    TimeoutException, ElementClickInterceptedException, StaleElementReferenceException,
    InvalidSessionIdException, WebDriverException
)
from webdriver_manager.chrome import ChromeDriverManager
from selenium.webdriver.chrome.service import Service as ChromeService
from selenium.webdriver.chrome.options import Options as ChromeOptions

URL = "https://consultas.tlaxcala.gob.mx/webapp/funcionales/busqueda.xhtml"


def ordered_charset(first: str | None):
    base = string.ascii_uppercase + string.digits
    if not first:
        return list(base)
    seen = set()
    pref = [c for c in first if c in base and not (c in seen or seen.add(c))]
    pref += [c for c in base if c not in seen]
    return pref


def generate_candidates(prefix: str, first1: str | None = None, first2: str | None = None):
    order1 = ordered_charset(first1)
    order2 = ordered_charset(first2)
    for c1 in order1:
        for c2 in order2:
            yield f"{prefix}{c1}-{c2}"


def build_driver(headless: bool = True, brave_binary: str | None = None):
    opts = ChromeOptions()
    if headless:
        opts.add_argument("--headless=new")
    opts.add_argument("--disable-gpu")
    opts.add_argument("--window-size=1400,2200")
    opts.add_argument("--no-sandbox")
    opts.add_argument("--disable-dev-shm-usage")
    opts.add_argument("--disable-blink-features=AutomationControlled")
    if brave_binary:
        opts.binary_location = brave_binary
    service = ChromeService(ChromeDriverManager().install())
    driver = webdriver.Chrome(service=service, options=opts)
    driver.set_page_load_timeout(60)
    return driver


def select_mode(driver, wait: WebDriverWait, mode: str):
    label = {
        "tramite": "Un trámite",
        "certificado": "Un certificado",
        "sello": "Un documento inscrito",
        "documento": "Un documento inscrito",
        "predio": "Un predio o sociedad",
        "folio": "Un predio o sociedad",
    }.get(mode.lower(), "Un trámite")

    trigger = wait.until(EC.element_to_be_clickable((
        By.XPATH, "//*[contains(@class,'ui-selectonemenu')][.//label or .//span]"
    )))
    try:
        trigger.click()
    except ElementClickInterceptedException:
        driver.execute_script("arguments[0].click();", trigger)
    time.sleep(0.2)

    item = wait.until(EC.element_to_be_clickable((
        By.XPATH, f"//li[contains(@class,'ui-selectonemenu-item')][contains(.,'{label.split()[1]}')]"
    )))
    try:
        item.click()
    except ElementClickInterceptedException:
        driver.execute_script("arguments[0].click();", item)
    time.sleep(0.2)


def locate_folio_input(driver, wait: WebDriverWait):
    xpaths = [
        "//label[contains(.,'Folio a buscar')]/following::input[1]",
        "//*[contains(@placeholder,'Folio') or contains(@aria-label,'Folio')][@type='text']",
        "//input[@type='text' and ancestor::div[.//text()[contains(.,'Consultar')]]]",
        "//input[@type='text']",
    ]
    last_exc = None
    for xp in xpaths:
        try:
            el = wait.until(EC.presence_of_element_located((By.XPATH, xp)))
            return el
        except TimeoutException as e:
            last_exc = e
            continue
    raise last_exc or TimeoutException("No se ubicó el campo de entrada.")


def click_button_by_text(driver, wait: WebDriverWait, text: str):
    xpaths = [
        f"//button[.//span[contains(.,'{text}')] or contains(.,'{text}')]",
        f"//a[contains(@class,'ui-button')][.//span[contains(.,'{text}')]]",
        f"//*[self::button or self::a][contains(.,'{text}')]",
    ]
    for xp in xpaths:
        try:
            btn = wait.until(EC.element_to_be_clickable((By.XPATH, xp)))
            try:
                btn.click()
            except ElementClickInterceptedException:
                driver.execute_script("arguments[0].click();", btn)
            return True
        except TimeoutException:
            continue
    return False


def click_limpiar_datos(driver, wait: WebDriverWait):
    click_button_by_text(driver, wait, "Limpiar datos")


def click_consultar(driver, wait: WebDriverWait):
    if not click_button_by_text(driver, wait, "Consultar"):
        raise TimeoutException("No se encontró el botón 'Consultar'.")


NOT_FOUND_PATTERNS = [
    "FOLIO DE TRÁMITE NO ENCONTRADO",
    "FOLIO DE TRAMITE NO ENCONTRADO",
    "FOLIO DE DOCUMENTO NO ENCONTRADO",
    "FOLIO DE CERTIFICADO NO ENCONTRADO",
    "FOLIO DE SELLO NO ENCONTRADO",
]

def any_not_found_banner(driver) -> bool:
    src = driver.page_source
    if any(p in src for p in NOT_FOUND_PATTERNS):
        return True
    low = src.lower()
    low_patterns = [p.lower() for p in NOT_FOUND_PATTERNS]
    return any(p in low for p in low_patterns)


def positive_indicators_present(driver, folio: str) -> bool:
    texts = [
        "Información del trámite",
        "Información del certificado",
        "Información del documento",
        "Información del sello",
        "Información del predio",
        "Información del folio",
        "Número de trámite",
        "Número de certificado",
        "Número de documento",
        "Número de sello",
        "Estado del trámite",
        "Estado del certificado",
        "Estado del documento",
        "Estado del sello",
    ]
    src = driver.page_source
    if any(t in src for t in texts):
        return True
    try:
        elems = driver.find_elements(By.XPATH,
            f"//a[contains(.,'{folio}')] | //span[contains(.,'{folio}')] | //td[contains(.,'{folio}')]")
        if elems and len(elems) > 0:
            return True
    except Exception:
        pass
    return False


def wait_for_result(driver, folio: str, max_wait: float = 12.0):
    end = time.time() + max_wait
    last_len = len(driver.page_source)
    while time.time() < end:
        if any_not_found_banner(driver):
            return "miss"
        if positive_indicators_present(driver, folio):
            return "hit"
        cur_len = len(driver.page_source)
        if cur_len != last_len:
            last_len = cur_len
        time.sleep(0.2)
    return "miss"  # estricto: sin indicadores => MISS


def recover_driver(existing_driver, headless, brave_binary):
    try:
        existing_driver.quit()
    except Exception:
        pass
    return build_driver(headless=headless, brave_binary=brave_binary)


def main():
    ap = argparse.ArgumentParser()
    ap.add_argument("--prefix", required=True, help="Prefijo, ej. TR-QWA4U-XMQN")
    ap.add_argument("--mode", default="tramite",
                    help="tramite | certificado | sello | predio (default: tramite)")
    ap.add_argument("--try-all-modes", action="store_true",
                    help="Prueba en los 4 modos por cada candidato")
    ap.add_argument("--delay", type=float, default=0.9, help="Delay adicional entre pasos")
    ap.add_argument("--headless", action="store_true", default=True, help="Headless (default)")
    ap.add_argument("--no-headless", action="store_false", dest="headless", help="Desactivar headless")
    ap.add_argument("--brave-binary", default=None, help="Ruta a Brave.exe si no usas Chrome")
    ap.add_argument("--out", default="found_matches.csv", help="Archivo CSV de resultados")
    ap.add_argument("--first-chars1", default=None, help="Prioriza caracteres para el penúltimo slot (A-Z0-9)")
    ap.add_argument("--first-chars2", default=None, help="Prioriza caracteres para el último slot (A-Z0-9)")
    ap.add_argument("--restart-every", type=int, default=60, help="Reinicia navegador cada N intentos (default 60)")
    ap.add_argument("--start-from", type=int, default=0, help="Índice inicial (0..1295) para reanudar")
    ap.add_argument("--max-retries", type=int, default=2, help="Reintentos por intento ante fallos de sesión")
    ap.add_argument("--debug-misses", type=int, default=0,
                    help="Guarda screenshot de las primeras N misses para diagnóstico (default 0)")
    args = ap.parse_args()

    out_csv = Path(args.out)
    out_csv.parent.mkdir(parents=True, exist_ok=True)

    modes = ["tramite", "certificado", "sello", "predio"] if args.try_all_modes else [args.mode]
    hits = []
    candidates = list(generate_candidates(args.prefix, args.first_chars1, args.first_chars2))

    if args.start_from > 0:
        candidates = candidates[args.start_from:]

    with out_csv.open("w", newline="", encoding="utf-8") as fout:
        writer = csv.writer(fout)
        writer.writerow(["folio", "modo", "timestamp", "screenshot_path"])

        driver = build_driver(headless=args.headless, brave_binary=args.brave_binary)
        wait = WebDriverWait(driver, 20)

        try:
            miss_shots = 0
            for idx, folio in enumerate(candidates, 1):
                if args.restart_every > 0 and (idx % args.restart_every == 0):
                    driver = recover_driver(driver, args.headless, args.brave_binary)
                    wait = WebDriverWait(driver, 20)

                for modo in modes:
                    attempt = 0
                    while True:
                        attempt += 1
                        try:
                            driver.get(URL)
                            select_mode(driver, wait, modo)
                            folio_input = locate_folio_input(driver, wait)

                            try:
                                folio_input.clear()
                            except StaleElementReferenceException:
                                folio_input = locate_folio_input(driver, wait)
                                folio_input.clear()

                            folio_input.send_keys(folio)
                            click_consultar(driver, wait)
                            time.sleep(args.delay)

                            status = wait_for_result(driver, folio, max_wait=12.0)

                            if status == "miss":
                                print(f"[MISS] {modo} :: {folio}")
                                if miss_shots < args.debug_misses:
                                    ts = time.strftime("%Y%m%d-%H%M%S")
                                    shot = Path(f"miss_{modo}_{folio}_{ts}.png")
                                    driver.save_screenshot(str(shot))
                                    miss_shots += 1
                                try:
                                    click_limpiar_datos(driver, wait)
                                except Exception:
                                    pass
                            elif status == "hit":
                                ts = time.strftime("%Y%m%d-%H%M%S")
                                shot = Path(f"match_{modo}_{folio}_{ts}.png")
                                driver.save_screenshot(str(shot))
                                writer.writerow([folio, modo, ts, str(shot)])
                                hits.append((folio, modo))
                                print(f"[HIT]  {modo} :: {folio} -> {shot}")
                            break

                        except (InvalidSessionIdException, WebDriverException) as e:
                            print(f"[WARN] Sesión inválida ({type(e).__name__}). Reiniciando driver... (try {attempt})")
                            if attempt > args.max_retries:
                                print("[ERROR] Máximos reintentos superados; sigo con el siguiente modo/candidato.")
                                break
                            driver = recover_driver(driver, args.headless, args.brave_binary)
                            wait = WebDriverWait(driver, 20)
                            time.sleep(1.0)
                            continue

            print("\n=== RESUMEN ===")
            if hits:
                print(f"{len(hits)} coincidencia(s) válidas (sin banners y con indicadores positivos):")
                for folio, modo in hits:
                    print(f" - {folio}  [{modo}]")
            else:
                print("No hubo coincidencias en los modos probados:", ", ".join(modes))
            print(f"CSV con resultados: {out_csv.resolve()}")

        finally:
            try:
                driver.quit()
            except Exception:
                pass


if __name__ == "__main__":
    sys.exit(main())
