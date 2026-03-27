#!/usr/bin/env python3
"""
Varroda API - Prodyflow adatbázis alapján
Futtatás: python3 api.py
Port: 5050
"""
from flask import Flask, jsonify, request
from flask_cors import CORS
import pymysql
import pymysql.cursors

app = Flask(__name__)
CORS(app)

DB = {
    'host': 'api.vivienvance.prodyflow.com',
    'port': 33068,
    'user': 'likar_viktor',
    'password': 'WzkS4dT76tWdKA5',
    'db': 'tenantvivienvance',
    'charset': 'utf8mb4',
    'cursorclass': pymysql.cursors.DictCursor,
}

def get_conn():
    return pymysql.connect(**DB)


# ─────────────────────────────────────────────
# GET /api/workers
# Varrónők listája (manufacturing_operation_crm_items-hez kapcsolt crm_items)
# ─────────────────────────────────────────────
@app.route('/api/workers')
def get_workers():
    conn = get_conn()
    try:
        cur = conn.cursor()
        cur.execute('''
            SELECT DISTINCT ci.id, ci.name, ci.image
            FROM crm_items ci
            JOIN manufacturing_operation_crm_items moci ON moci.crm_item_id = ci.id
            WHERE ci.deleted_at IS NULL
            ORDER BY ci.name
        ''')
        workers = []
        for r in cur.fetchall():
            name = r['name'] or ''
            parts = name.strip().split()
            initials = ''.join(p[0].upper() for p in parts if p)[:2]
            workers.append({
                'id': r['id'],
                'name': name,
                'initials': initials,
                'image': None,
            })
        return jsonify(workers)
    finally:
        conn.close()


# ─────────────────────────────────────────────
# GET /api/manufacturing-ops
# Összes gyártási művelet + hozzárendelt varrónők
# ─────────────────────────────────────────────
@app.route('/api/manufacturing-ops')
def get_manufacturing_ops():
    conn = get_conn()
    try:
        cur = conn.cursor()
        cur.execute('''
            SELECT mo.id, mo.name, mo.sort_description, mo.cost
            FROM manufacturing_operations mo
            WHERE mo.is_hidden != 1 OR mo.is_hidden IS NULL
            ORDER BY mo.name
        ''')
        ops = cur.fetchall()

        # Varrónők per op
        cur.execute('''
            SELECT moci.manufacturing_operation_id, ci.id as worker_id, ci.name as worker_name
            FROM manufacturing_operation_crm_items moci
            JOIN crm_items ci ON ci.id = moci.crm_item_id
            WHERE ci.deleted_at IS NULL
        ''')
        workers_map = {}
        for r in cur.fetchall():
            oid = r['manufacturing_operation_id']
            if oid not in workers_map:
                workers_map[oid] = []
            workers_map[oid].append({'id': r['worker_id'], 'name': r['worker_name']})

        result = []
        for op in ops:
            result.append({
                'id': op['id'],
                'name': op['name'],
                'description': op['sort_description'],
                'cost': float(op['cost']) if op['cost'] else None,
                'workers': workers_map.get(op['id'], []),
            })
        return jsonify(result)
    finally:
        conn.close()


# ─────────────────────────────────────────────
# GET /api/production-ops
# Aktív gyártási warehouse_operations (type=6)
# ─────────────────────────────────────────────
@app.route('/api/production-ops')
def get_production_ops():
    conn = get_conn()
    try:
        cur = conn.cursor()
        cur.execute('''
            SELECT wo.id, wo.title, wo.status, wo.created_at, wo.deadline_date, wo.pretty_id
            FROM warehouse_operations wo
            WHERE wo.operation_type = 6
              AND (wo.status != -2 OR wo.status IS NULL)
            ORDER BY wo.created_at DESC
            LIMIT 50
        ''')
        ops = []
        for r in cur.fetchall():
            ops.append({
                'id': r['id'],
                'title': r['title'],
                'status': r['status'],
                'created_at': str(r['created_at']) if r['created_at'] else None,
                'deadline': str(r['deadline_date']) if r['deadline_date'] else None,
                'pretty_id': r['pretty_id'],
            })
        return jsonify(ops)
    finally:
        conn.close()


# ─────────────────────────────────────────────
# GET /api/production-ops/<id>
# Egy gyártás részletei: termékek, méretek, manufacturing ops
# ─────────────────────────────────────────────
@app.route('/api/production-ops/<int:op_id>')
def get_production_op(op_id):
    conn = get_conn()
    try:
        cur = conn.cursor()

        # Gyártás alap adatok
        cur.execute('''
            SELECT id, title, status, created_at, deadline_date
            FROM warehouse_operations
            WHERE id = %s AND operation_type = 6
        ''', (op_id,))
        op = cur.fetchone()
        if not op:
            return jsonify({'error': 'Not found'}), 404

        # Termékek ebben a gyártásban
        cur.execute('''
            SELECT
                io.id as item_op_id,
                io.quantity,
                io.status as item_status,
                io.start_date,
                io.deadline_date,
                io.done_in,
                io.done_out,
                pv.id as variant_id,
                pv.sku,
                pv.product_id
            FROM item_operations io
            JOIN items it ON it.id = io.item_id
            JOIN product_variants pv ON pv.id = it.product_variant_id
            WHERE io.warehouse_operation_id = %s
            ORDER BY io.id
        ''', (op_id,))
        items_raw = cur.fetchall()

        if not items_raw:
            return jsonify({
                'id': op['id'],
                'title': op['title'],
                'status': op['status'],
                'created_at': str(op['created_at']),
                'deadline': str(op['deadline_date']) if op['deadline_date'] else None,
                'items': [],
                'manufacturing_ops': [],
            })

        # Terméknevek
        product_ids = list(set(r['product_id'] for r in items_raw))
        product_names = {}
        if product_ids:
            placeholders = ','.join(['%s'] * len(product_ids))
            cur.execute(f'''
                SELECT product_id, name
                FROM product_translations
                WHERE product_id IN ({placeholders})
                  AND language_code = 'hu'
            ''', product_ids)
            for r in cur.fetchall():
                product_names[r['product_id']] = r['name']

        # Méretek / karakterisztikák per variant
        variant_ids = list(set(r['variant_id'] for r in items_raw))
        variant_sizes = {}
        if variant_ids:
            placeholders = ','.join(['%s'] * len(variant_ids))
            cur.execute(f'''
                SELECT pc.variant_id, cvt.val_text as size_val
                FROM product_characteristics pc
                JOIN characteristics_value_translations cvt
                    ON cvt.characteristics_value_id = pc.characteristics_value_id
                    AND cvt.language_code = 'hu'
                WHERE pc.variant_id IN ({placeholders})
                ORDER BY pc.variant_id
            ''', variant_ids)
            for r in cur.fetchall():
                vid = r['variant_id']
                if vid not in variant_sizes:
                    variant_sizes[vid] = []
                variant_sizes[vid].append(r['size_val'])

        # Összesített méret → darabszám
        size_totals = {}
        items = []
        for r in items_raw:
            sizes = variant_sizes.get(r['variant_id'], [])
            size_str = ' / '.join(sizes) if sizes else r['sku']
            qty = int(r['quantity']) if r['quantity'] else 0
            if size_str not in size_totals:
                size_totals[size_str] = 0
            size_totals[size_str] += qty

            items.append({
                'item_op_id': r['item_op_id'],
                'variant_id': r['variant_id'],
                'sku': r['sku'],
                'product_name': product_names.get(r['product_id'], r['sku']),
                'quantity': qty,
                'sizes': sizes,
                'status': r['item_status'],
                'start_date': str(r['start_date']) if r['start_date'] else None,
                'deadline': str(r['deadline_date']) if r['deadline_date'] else None,
                'done_in': r['done_in'],
                'done_out': r['done_out'],
            })

        # Manufacturing operations a termékekhez
        if product_ids:
            placeholders = ','.join(['%s'] * len(product_ids))
            cur.execute(f'''
                SELECT
                    pmo.id, pmo.product_id, pmo.order, pmo.time, pmo.description, pmo.type,
                    mo.id as mo_id, mo.name as op_name, mo.sort_description
                FROM product_manufacturing_operations pmo
                JOIN manufacturing_operations mo ON mo.id = pmo.manufacturing_operation_id
                WHERE pmo.product_id IN ({placeholders})
                  AND (pmo.is_hidden != 1 OR pmo.is_hidden IS NULL)
                ORDER BY pmo.product_id, pmo.order
            ''', product_ids)
            mfg_ops = []
            seen = set()
            for r in cur.fetchall():
                key = (r['mo_id'], r['product_id'])
                if key not in seen:
                    seen.add(key)
                    mfg_ops.append({
                        'id': r['mo_id'],
                        'pmo_id': r['id'],
                        'product_id': r['product_id'],
                        'order': r['order'],
                        'name': r['op_name'],
                        'description': r['sort_description'] or r['description'],
                        'time_sec': r['time'],
                        'type': r['type'],
                    })
        else:
            mfg_ops = []

        total_qty = sum(i['quantity'] for i in items)
        return jsonify({
            'id': op['id'],
            'title': op['title'],
            'status': op['status'],
            'created_at': str(op['created_at']),
            'deadline': str(op['deadline_date']) if op['deadline_date'] else None,
            'total_qty': total_qty,
            'size_totals': size_totals,
            'items': items,
            'manufacturing_ops': mfg_ops,
        })
    finally:
        conn.close()


# ─────────────────────────────────────────────
# GET /api/workers/<id>/ops
# Egy varrónő összes hozzárendelt manufacturing_operation
# ─────────────────────────────────────────────
@app.route('/api/workers/<int:worker_id>/ops')
def get_worker_ops(worker_id):
    conn = get_conn()
    try:
        cur = conn.cursor()
        cur.execute('''
            SELECT mo.id, mo.name, mo.sort_description, mo.cost
            FROM manufacturing_operations mo
            JOIN manufacturing_operation_crm_items moci ON moci.manufacturing_operation_id = mo.id
            WHERE moci.crm_item_id = %s
              AND (mo.is_hidden != 1 OR mo.is_hidden IS NULL)
            ORDER BY mo.name
        ''', (worker_id,))
        ops = []
        for r in cur.fetchall():
            ops.append({
                'id': r['id'],
                'name': r['name'],
                'description': r['sort_description'],
                'cost': float(r['cost']) if r['cost'] else None,
            })
        return jsonify(ops)
    finally:
        conn.close()


# ─────────────────────────────────────────────
# GET /api/schedule
# Timeline: varrónők + aktív gyártásaik a mai napra
# ─────────────────────────────────────────────
@app.route('/api/schedule')
def get_schedule():
    conn = get_conn()
    try:
        cur = conn.cursor()

        # Varrónők
        cur.execute('''
            SELECT DISTINCT ci.id, ci.name
            FROM crm_items ci
            JOIN manufacturing_operation_crm_items moci ON moci.crm_item_id = ci.id
            WHERE ci.deleted_at IS NULL
            ORDER BY ci.name
        ''')
        workers = []
        for r in cur.fetchall():
            name = r['name'] or ''
            parts = name.strip().split()
            initials = ''.join(p[0].upper() for p in parts if p)[:2]
            workers.append({'id': r['id'], 'name': name, 'initials': initials})

        # Aktív gyártások
        cur.execute('''
            SELECT wo.id, wo.title, wo.status, wo.created_at, wo.deadline_date
            FROM warehouse_operations wo
            WHERE wo.operation_type = 6
              AND (wo.status IS NULL OR wo.status NOT IN (-2, 7))
            ORDER BY wo.created_at DESC
            LIMIT 20
        ''')
        prod_ops = {}
        for r in cur.fetchall():
            prod_ops[r['id']] = {
                'id': r['id'],
                'title': r['title'],
                'status': r['status'],
                'deadline': str(r['deadline_date']) if r['deadline_date'] else None,
            }

        if not prod_ops:
            return jsonify({'workers': workers, 'production_ops': [], 'assignments': []})

        prod_op_ids = list(prod_ops.keys())

        # Manufacturing ops per production op (via products)
        # worker → manufacturing_op → production op context
        # Get which manufacturing ops exist in active production ops
        placeholders = ','.join(['%s'] * len(prod_op_ids))
        cur.execute(f'''
            SELECT DISTINCT
                pmo.manufacturing_operation_id as mo_id,
                io.warehouse_operation_id as prod_op_id,
                mo.name as op_name,
                pmo.time as time_sec,
                COUNT(io.id) as item_count,
                SUM(io.quantity) as total_qty
            FROM item_operations io
            JOIN items it ON it.id = io.item_id
            JOIN product_variants pv ON pv.id = it.product_variant_id
            JOIN product_manufacturing_operations pmo ON pmo.product_id = pv.product_id
                AND (pmo.is_hidden != 1 OR pmo.is_hidden IS NULL)
            JOIN manufacturing_operations mo ON mo.id = pmo.manufacturing_operation_id
            WHERE io.warehouse_operation_id IN ({placeholders})
            GROUP BY pmo.manufacturing_operation_id, io.warehouse_operation_id, mo.name, pmo.time
        ''', prod_op_ids)
        mo_prod_context = {}
        for r in cur.fetchall():
            mo_id = r['mo_id']
            if mo_id not in mo_prod_context:
                mo_prod_context[mo_id] = []
            mo_prod_context[mo_id].append({
                'prod_op_id': r['prod_op_id'],
                'prod_op_title': prod_ops[r['prod_op_id']]['title'],
                'op_name': r['op_name'],
                'time_sec': r['time_sec'],
                'total_qty': int(r['total_qty']) if r['total_qty'] else 0,
            })

        # Worker → manufacturing_ops → production context
        assignments = []
        cur.execute('''
            SELECT moci.crm_item_id as worker_id, moci.manufacturing_operation_id as mo_id,
                   mo.name as op_name, mo.sort_description
            FROM manufacturing_operation_crm_items moci
            JOIN manufacturing_operations mo ON mo.id = moci.manufacturing_operation_id
            WHERE (mo.is_hidden != 1 OR mo.is_hidden IS NULL)
        ''')
        worker_mo_map = {}
        for r in cur.fetchall():
            wid = r['worker_id']
            if wid not in worker_mo_map:
                worker_mo_map[wid] = []
            worker_mo_map[wid].append({
                'mo_id': r['mo_id'],
                'op_name': r['op_name'],
                'description': r['sort_description'],
                'production': mo_prod_context.get(r['mo_id'], []),
            })

        for w in workers:
            assignments.append({
                'worker_id': w['id'],
                'worker_name': w['name'],
                'worker_initials': w['initials'],
                'ops': worker_mo_map.get(w['id'], []),
            })

        return jsonify({
            'workers': workers,
            'production_ops': list(prod_ops.values()),
            'assignments': assignments,
        })
    finally:
        conn.close()


if __name__ == '__main__':
    app.run(host='0.0.0.0', port=5050, debug=False)
