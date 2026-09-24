# test_ova_formula.py — prueba suelta de la fórmula, sin tocar el bot en producción
from ova_formula import juegos_de_hoy, lineup_confirmado, analizar_juego
import json

SEASON = 2026

juegos = juegos_de_hoy()
print(f"Juegos encontrados hoy: {len(juegos)}")

juego_listo = None
for j in juegos:
    print(f"- {j['away_name']} @ {j['home_name']} · lineup confirmado: {lineup_confirmado(j)}")
    if lineup_confirmado(j) and juego_listo is None:
        juego_listo = j

if juego_listo is None:
    print("\nNingún juego con lineup confirmado todavía (normal si es temprano en el día).")
else:
    print(f"\nAnalizando: {juego_listo['away_name']} @ {juego_listo['home_name']}")
    resultado = analizar_juego(juego_listo, SEASON)
    print(json.dumps(resultado, indent=2, ensure_ascii=False))
