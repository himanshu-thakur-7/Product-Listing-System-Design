import os
import json
from functools import wraps
from flask import Flask, request, jsonify, abort
import psycopg2
from psycopg2 import sql
from psycopg2.pool import SimpleConnectionPool
import atexit

# -------------------------
# Configuration (env or defaults)
# -------------------------
PRIMARY_HOST = os.getenv("PRIMARY_HOST", "localhost")
PRIMARY_PORT = int(os.getenv("PRIMARY_PORT", 5432))
PRIMARY_DB = os.getenv("PRIMARY_DB", "postgres")
PRIMARY_USER = os.getenv("PRIMARY_USER", "user")
PRIMARY_PASSWORD = os.getenv("PRIMARY_PASSWORD", "password")

REPLICA_HOST = os.getenv("REPLICA_HOST", "localhost")
REPLICA_PORT = int(os.getenv("REPLICA_PORT", 5433))
REPLICA_DB = os.getenv("REPLICA_DB", "postgres")
REPLICA_USER = os.getenv("REPLICA_USER", "user")
REPLICA_PASSWORD = os.getenv("REPLICA_PASSWORD", "password")

# Admin token for simple protection (set a strong value in production)
ADMIN_TOKEN = os.getenv("ADMIN_TOKEN", "token")

# Pool sizes
MINCONN = int(os.getenv("DB_POOL_MIN", 1))
MAXCONN = int(os.getenv("DB_POOL_MAX", 10))

# -------------------------
# Setup connection pools
# -------------------------
primary_pool = SimpleConnectionPool(
    MINCONN, MAXCONN,
    host=PRIMARY_HOST, port=PRIMARY_PORT,
    dbname=PRIMARY_DB, user=PRIMARY_USER, password=PRIMARY_PASSWORD
)

replica_pool = SimpleConnectionPool(
    MINCONN, MAXCONN,
    host=REPLICA_HOST, port=REPLICA_PORT,
    dbname=REPLICA_DB, user=REPLICA_USER, password=REPLICA_PASSWORD
)

# -------------------------
# Flask app
# -------------------------
app = Flask(__name__)

def admin_required(f):
    @wraps(f)
    def decorated(*args, **kwargs):
        # Accept token via header X-Admin-Token or Authorization: Bearer <token>
        token = request.headers.get("X-Admin-Token")
        if not token:
            auth = request.headers.get("Authorization", "")
            if auth.startswith("Bearer "):
                token = auth.split(" ", 1)[1].strip()
        if not token or token != ADMIN_TOKEN:
            return jsonify({"error": "unauthorized"}), 401
        return f(*args, **kwargs)
    return decorated

# Utility to use a pool connection safely
def run_query(pool, query, params=None, fetch=False):
    conn = pool.getconn()
    try:
        with conn.cursor() as cur:
            cur.execute(query, params or ())
            if fetch:
                rows = cur.fetchall()
                # convert to Python types (tuples -> lists)
                return rows
            # If writing, commit
            conn.commit()
            return None
    finally:
        pool.putconn(conn)

# -------------------------
# Endpoints
# -------------------------

@app.route("/products", methods=["GET"])
def get_products():
    """
    Read from the replica.
    Optional query params: ?limit=50&offset=0
    """
    try:
        limit = int(request.args.get("limit", 100))
        offset = int(request.args.get("offset", 0))
    except ValueError:
        return jsonify({"error": "limit/offset must be integers"}), 400

    q = sql.SQL("SELECT product_id, product_name, price, product_image_url FROM products ORDER BY product_id LIMIT %s OFFSET %s")
    try:
        rows = run_query(replica_pool, q.as_string(replica_pool._conn_kwargs['dsn']) if False else q.as_string(psycopg2.extensions.adapt('')), (limit, offset), fetch=True)
    except Exception as e:
        # fallback: run_raw using parameterized string (because psycopg2.sql used incorrectly above)
        # We'll do the simple parameterized query directly:
        try:
            rows = run_query(
                replica_pool,
                "SELECT product_id, product_name, price, product_image_url FROM products ORDER BY product_id LIMIT %s OFFSET %s",
                (limit, offset),
                fetch=True
            )
        except Exception as ex:
            return jsonify({"error": "failed to query replica", "detail": str(ex)}), 500

    # Convert rows to dicts
    products = [
        {
            "product_id": r[0],
            "product_name": r[1],
            "price": r[2],
            "product_image_url": r[3]
        } for r in rows
    ]
    return jsonify({"products": products})

@app.route("/admin/products", methods=["POST"])
def create_product():
    """
    Create product on the primary.
    JSON body: { "product_name": "...", "price": 123, "product_image_url": "..." }
    """
    data = request.get_json(force=True, silent=True)
    if not data:
        return jsonify({"error": "missing json body"}), 400

    name = data.get("product_name")
    price = data.get("price")
    image = data.get("product_image_url")

    if not name or not isinstance(name, str):
        return jsonify({"error": "product_name required"}), 400
    try:
        price = int(price)
    except Exception:
        return jsonify({"error": "price must be an integer"}), 400

    query = """
        INSERT INTO products (product_name, price, product_image_url)
        VALUES (%s, %s, %s)
        RETURNING product_id, product_name, price, product_image_url;
    """
    try:
        rows = run_query(primary_pool, query, (name, price, image), fetch=True)
        created = rows[0] if rows else None
        if created:
            return jsonify({
                "product": {
                    "product_id": created[0],
                    "product_name": created[1],
                    "price": created[2],
                    "product_image_url": created[3]
                }
            }), 201
        else:
            return jsonify({"error": "insert failed"}), 500
    except Exception as e:
        return jsonify({"error": "failed to insert", "detail": str(e)}), 500

@app.route("/admin/products/<int:product_id>", methods=["PATCH"])
def update_product(product_id):
    """
    Patch product fields on the primary. Accepts subset of fields:
    { "product_name": "...", "price": 123, "product_image_url": "..." }
    """
    print(product_id)
    data = request.get_json(force=True, silent=True)
    if not data:
        return jsonify({"error": "missing json body"}), 400

    allowed = {"product_name", "price", "product_image_url"}
    updates = {k: data[k] for k in data.keys() & allowed}

    if not updates:
        return jsonify({"error": "no updatable fields provided"}), 400

    # Build dynamic SQL safely
    set_clauses = []
    params = []
    for idx, (col, val) in enumerate(updates.items(), start=1):
        set_clauses.append(sql.SQL("{} = %s").format(sql.Identifier(col)))
        if col == "price":
            try:
                val = int(val)
            except Exception:
                return jsonify({"error": "price must be integer"}), 400
        params.append(val)
    params.append(product_id)

    query = sql.SQL("UPDATE products SET ") + sql.SQL(", ").join(set_clauses) + sql.SQL(" WHERE product_id = %s RETURNING product_id, product_name, price, product_image_url;")

    # As psycopg2.sql objects require a connection context to compose, we'll execute with cursor executing a string with params:
    # Build textual query and param order:
    set_texts = [f"{col} = %s" for col in updates.keys()]
    final_query = f"UPDATE products SET {', '.join(set_texts)} WHERE product_id = %s RETURNING product_id, product_name, price, product_image_url;"
    print(final_query, params)
    try:
        rows = run_query(primary_pool, final_query, tuple(params), fetch=True)
        if rows:
            r = rows[0]
            print(r)
            return jsonify({"product": {"product_id": r[0], "product_name": r[1], "price": r[2], "product_image_url": r[3]}})
        else:
            return jsonify({"error": "product not found"}), 404
    except Exception as e:
        return jsonify({"error": "failed to update", "detail": str(e)}), 500

# Simple health check
@app.route("/health", methods=["GET"])
def health():
    return jsonify({"status": "ok"})

# -------------------------
# Cleanup on shutdown
# -------------------------
@atexit.register
def close_pools(exception):
    try:
        if primary_pool:
            primary_pool.closeall()
    except Exception:
        pass
    try:
        if replica_pool:
            replica_pool.closeall()
    except Exception:
        pass

# -------------------------
# Main
# -------------------------
if __name__ == "__main__":
    port = int(os.getenv("FLASK_PORT", 8080))
    debug = os.getenv("FLASK_DEBUG", "0") == "1"
    app.run(host="0.0.0.0", port=port, debug=debug)
