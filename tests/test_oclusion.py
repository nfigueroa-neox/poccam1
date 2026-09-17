"""Prueba de la verificacion anti-oclusion.

Verifica que:
  1. Deshabilitada -> nunca marca nada
  2. Un cambio localizado (digitos) NO se marca
  3. Un cambio que cubre casi todo SI se marca
  4. El umbral configurable cambia el comportamiento
"""
import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent.parent / "worker"))

from backend.detector import DetectorCambios  # noqa: E402


def caso(nombre, area, total, esperado, **kw):
    d = DetectorCambios(**kw)
    marco, motivo = d.evaluar_oclusion(area, total)
    ok = marco == esperado
    estado = "OK " if ok else "FALLO"
    print(f"  [{estado}] {nombre}")
    print(f"          area={area}/{total} -> {'marca' if marco else 'no marca'}"
          f"{' (' + motivo + ')' if motivo else ''}")
    return ok


def main():
    print("=" * 66)
    print("PRUEBA: verificacion anti-oclusion")
    print("=" * 66)

    total = 552 * 102  # ROI real de la XGQ-100F
    print(f"\nROI de referencia: 552x102 = {total} px\n")

    resultados = []

    print("--- 1. DESHABILITADA (default) ---")
    resultados.append(caso("cambio enorme con anti_oclusion=False",
                           int(total * 0.99), total, False,
                           anti_oclusion=False))

    print("\n--- 2. HABILITADA, umbral por defecto (0.85) ---")
    resultados.append(caso("digitos cambian (5% del area)",
                           int(total * 0.05), total, False,
                           anti_oclusion=True))
    resultados.append(caso("cambio mediano (40% del area)",
                           int(total * 0.40), total, False,
                           anti_oclusion=True))
    resultados.append(caso("casi todo cambia (90% del area)",
                           int(total * 0.90), total, True,
                           anti_oclusion=True))
    resultados.append(caso("obstruccion total (100% del area)",
                           total, total, True,
                           anti_oclusion=True))

    print("\n--- 3. Umbral mas estricto (0.5) ---")
    resultados.append(caso("cambio del 60% marca con umbral 0.5",
                           int(total * 0.60), total, True,
                           anti_oclusion=True, anti_oclusion_area_max=0.5))
    resultados.append(caso("cambio del 40% no marca con umbral 0.5",
                           int(total * 0.40), total, False,
                           anti_oclusion=True, anti_oclusion_area_max=0.5))

    print("\n--- 4. Casos borde ---")
    resultados.append(caso("area 0 (sin cambio)",
                           0, total, False, anti_oclusion=True))
    resultados.append(caso("total 0 (sin ROI)",
                           total, 0, False, anti_oclusion=True))

    print("\n" + "=" * 66)
    ok = sum(resultados)
    print(f"RESULTADO: {ok}/{len(resultados)} pruebas OK")
    if ok == len(resultados):
        print("*** TODAS LAS PRUEBAS PASARON ***")
        return 0
    print("*** HAY FALLOS ***")
    return 1


if __name__ == "__main__":
    sys.exit(main())
