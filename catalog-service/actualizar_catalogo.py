"""Genera games_seed.json con juegos reales de la tienda de Steam.

Fuente: API publica de la tienda de Steam (la misma que usa store.steampowered.com):
    https://store.steampowered.com/api/appdetails?appids=<id>&cc=mx&l=spanish
De ahi se toman el nombre, la desarrolladora, el precio en MXN, el descuento vigente, la fecha de
lanzamiento, la descripcion en espanol y la portada oficial (header.jpg).

El genero principal se asigna a mano (Steam clasifica casi todo como "Accion"); los generos de Steam se
conservan como etiquetas. El microservicio lee el JSON generado, asi que no depende de Steam en tiempo
de ejecucion.

Uso:  python actualizar_catalogo.py
"""
import html
import json
import re
import time
from pathlib import Path

import requests

# appid de Steam -> genero principal en la tienda
JUEGOS = {
    1245620: "RPG", 1091500: "RPG", 292030: "RPG", 1086940: "RPG",
    1145360: "Roguelike", 588650: "Roguelike", 1794680: "Roguelike",
    367520: "Plataformas", 268910: "Plataformas", 504230: "Plataformas",
    1174180: "Aventura", 1593500: "Aventura", 814380: "Aventura",
    2050650: "Terror", 739630: "Terror", 1966720: "Terror",
    730: "Shooter", 553850: "Shooter",
    289070: "Estrategia", 570: "Estrategia",
    2379780: "Cartas", 646570: "Cartas",
    892970: "Supervivencia", 105600: "Supervivencia",
    413150: "Simulación", 620: "Puzzle",
}

# Colores de respaldo por genero (se ven si la portada de Steam no carga)
COLORES = {
    "RPG": ("#3a1c71", "#d76d77"), "Roguelike": ("#614385", "#516395"), "Plataformas": ("#1d976c", "#93f9b9"),
    "Aventura": ("#0f2027", "#f7971e"), "Terror": ("#232526", "#414345"), "Shooter": ("#000428", "#004e92"),
    "Estrategia": ("#83a4d4", "#b6fbff"), "Cartas": ("#8e2de2", "#4a00e0"), "Supervivencia": ("#56ab2f", "#a8e063"),
    "Simulación": ("#f12711", "#f5af19"), "Puzzle": ("#c79081", "#dfa579"),
}
CATEGORIAS_UTILES = ["Un jugador", "Multijugador", "Cooperativo", "JcJ", "Cooperativo en línea"]


def slug(nombre):
    nombre = re.sub(r"[’'™®]", "", nombre.lower())
    return re.sub(r"[^a-z0-9]+", "-", nombre).strip("-")


def limpiar(texto):
    return re.sub(r"\s+", " ", re.sub(r"<[^>]+>", " ", html.unescape(texto or ""))).strip()


def main():
    juegos = []
    for appid, genero in JUEGOS.items():
        r = requests.get("https://store.steampowered.com/api/appdetails",
                         params={"appids": appid, "cc": "mx", "l": "spanish"},
                         headers={"User-Agent": "PixelVault-practica-escolar/1.0"}, timeout=20)
        info = r.json()[str(appid)]
        if not info.get("success"):
            print("sin datos:", appid)
            continue
        d = info["data"]
        precio = d.get("price_overview") or {}
        titulo = re.sub(r"[™®]", "", d["name"]).strip()
        anio = re.search(r"\d{4}", d.get("release_date", {}).get("date", "")) or re.search(r"\d{4}", "2020")
        generos_steam = [g["description"] for g in d.get("genres", [])]
        categorias = [c["description"] for c in d.get("categories", []) if c["description"] in CATEGORIAS_UTILES]
        etiquetas = list(dict.fromkeys(generos_steam + categorias))[:5]
        juegos.append({
            "id": slug(titulo),
            "steam_appid": appid,
            "title": titulo,
            "developer": (d.get("developers") or ["—"])[0],
            "genre": genero,
            "price_cents": 0 if d.get("is_free") else precio.get("initial", 0),
            "discount_pct": precio.get("discount_percent", 0),
            "release_year": int(anio.group()),
            "tags": etiquetas,
            "description": limpiar(d.get("short_description")),
            "header_image": d.get("header_image", "").split("?")[0],
            "color1": COLORES[genero][0],
            "color2": COLORES[genero][1],
        })
        print(f"{appid:>8}  {titulo:<45} {genero:<13} ${juegos[-1]['price_cents'] / 100:>9,.2f}")
        time.sleep(1.5)  # la API publica limita a ~200 peticiones cada 5 minutos

    destino = Path(__file__).with_name("games_seed.json")
    destino.write_text(json.dumps({"source": "Steam Store API (cc=mx, l=spanish)",
                                   "fetched_at": time.strftime("%Y-%m-%d"),
                                   "games": juegos}, ensure_ascii=False, indent=1), encoding="utf8")
    print(f"{len(juegos)} juegos guardados en {destino.name}")


if __name__ == "__main__":
    main()
