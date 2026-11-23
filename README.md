# Segunda práctica de IA Distribuida · Implementación clásica

La simulación reproduce una subasta holandesa de pescado. Cada operador anuncia un ejemplar a un precio inicial y lo rebaja si vence el turno sin una compra. Cada comerciante intenta conseguir una merluza, un lenguado y un atún, y comprar más unidades del tipo que prefiere.

## Cómo ejecutar la simulación

Desde la carpeta que contiene `subasta_clasica.py`, ejecuta estos comandos con Python 3.12.

```bash
python -m pip install -r requirements.txt
python subasta_clasica.py --seed 42
```

La configuración inicial usa dos operadores con ocho pescados cada uno, tres comerciantes con 100 unidades de presupuesto por comerciante, una rebaja de 3 unidades y un intervalo de 0,15 segundos por precio. Las preferencias y los tipos de pescado se eligen al azar. La semilla fija esos sorteos. El orden de llegada de los mensajes puede variar entre ejecuciones.

```bash
python subasta_clasica.py --operators 3 --merchants 4 --fish-range 5 8 --budgets 80 100 100 120 --seed 42
python subasta_clasica.py --help
```

Cada ejecución genera `setup [fecha].csv` y `log [fecha].csv` en el directorio actual o en el que indiques con `--output-dir`. El primer CSV guarda el identificador, la preferencia y el presupuesto inicial de cada comerciante. El segundo recoge una fila por pescado, ordenada por identificador. Si no se vendió, el precio de venta es 0 y la celda del comerciante queda vacía.

## Precios y estrategia de compra

| Tipo | Pescado | Precio inicial | Precio mínimo |
| --- | --- | ---: | ---: |
| H | Merluza | 32 | 12 |
| S | Lenguado | 28 | 10 |
| T | Atún | 38 | 15 |

Si todavía le falta un tipo, el comerciante lo busca a un precio intermedio entre el mínimo y el inicial. Acepta pagar más por su favorito. Cuando ya tiene una unidad de ese tipo, compra otra solo si el precio está en el tercio inferior del intervalo y puede seguir reservando el precio mínimo de los tipos que le faltan. Si no puede reservar suficiente para completar el surtido, todavía puede comprar un tipo que le falte a su precio mínimo si dispone del importe. El precio mínimo orienta la decisión. La disponibilidad y la competencia determinan si puede completar el surtido.

## Protocolo

| Mensaje | Emisor → receptor | Función |
| --- | --- | --- |
| `SYNC` / `READY` | Operador ↔ comerciantes | Comprueban que las suscripciones están activas antes de subastar. |
| Oferta | Operador → todos los comerciantes | Anuncia el operador, el producto, el tipo y el precio actual a través de PUB/SUB. |
| `BUY` | Comerciante → operador | Solicita comprar a través de PUSH/PULL e indica el comerciante y el precio observado. |
| `SOLD` | Operador → todos los comerciantes | Confirma la compra al primer comerciante que envió un `BUY` válido. |
| `UNSOLD` | Operador → todos los comerciantes | Cierra la subasta cuando nadie compra durante el turno del precio mínimo. |

Los operadores pueden anunciar pescados simultáneamente. El precio de la solicitud permite descartar mensajes de rondas anteriores. El comerciante reserva localmente el importe de cada solicitud pendiente y se lo descuenta del saldo al recibir la confirmación de su compra. Las rebajas y los mensajes de cierre liberan las reservas correspondientes. El proceso principal arranca los agentes, espera a que finalicen y escribe los CSV. Cada agente decide en su propio proceso.

Los patrones de comunicación y los temporizadores se describen en la [documentación de osBrain](https://osbrain.readthedocs.io/en/stable/).

## Resultados de las pruebas

| Escenario | Presupuestos iniciales | Vendidos | Sin vender | Comerciantes con H, S y T |
| --- | --- | ---: | ---: | ---: |
| Presupuestos iguales | 100, 100, 100 | 14 | 2 | 2 de 3 |
| Presupuestos distintos | 45, 75, 180 | 13 | 3 | 2 de 3 |
| Saldo cero | 0 | 0 | 5 | 0 de 1 |
| Cuatro operadores y un comerciante | 15 | 1 | 15 | 0 de 1 |

Los dos primeros escenarios usan la semilla 42, el mismo inventario, las mismas preferencias y un presupuesto total de 300. En la ejecución con presupuestos distintos, el comerciante con 180 unidades compró siete merluzas, frente a tres con un presupuesto de 100. El comerciante con 45 unidades compró un atún y acabó con 22 unidades de saldo. El orden de llegada de las solicitudes influye en la adjudicación, por lo que estas cifras describen las ejecuciones incluidas.

En el escenario sin presupuesto, la rebaja se fijó en 100 para comprobar que el precio se ajustaba al mínimo aunque la resta lo sobrepasara. Con cuatro operadores, el único comerciante compró un lenguado a 10 y acabó con 5 de saldo. Se verificó en los CSV que cada pescado aparece una sola vez, los precios de venta se ajustan a las rondas y ningún comerciante supera su presupuesto.

Las pruebas se ejecutaron con procesos reales de osBrain y Python 3.12 en Linux. El entorno de prueba restringía la apertura de sockets, así que se ajustó la apertura de TCP durante las pruebas. El programa entregado utiliza la configuración de transporte predeterminada de osBrain.
