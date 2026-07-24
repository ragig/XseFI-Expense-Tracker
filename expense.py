from datetime import date, datetime, timedelta
from functools import wraps
import os
import sqlite3

from flask import Flask, flash, g, redirect, render_template, request, session, url_for
from werkzeug.security import check_password_hash, generate_password_hash


app = Flask(__name__)
app.config["SECRET_KEY"] = os.environ.get("SECRET_KEY", "dev-expense-tracker-secret")
app.config["DATABASE"] = os.path.join(app.instance_path, "expense_tracker.sqlite")

DEFAULT_CATEGORIES = [
    "Books",
    "Phone",
    "Food",
    "Shopping",
    "Travel",
    "Bills",
    "Health",
    "Education",
    "Entertainment",
    "Other",
]

DASHBOARD_CATEGORY_ORDER = [
    "Food",
    "Phone",
    "Books",
    "Shopping",
    "Travel",
    "Bills",
    "Health",
    "Education",
    "Entertainment",
    "Other",
]

ACCOUNT_TYPES = ["Card", "Cash", "Cash in Hand", "Savings"]
LOW_BALANCE_LIMIT = 1000


TABLE_COLUMNS = {
    "users": {
        "username": "TEXT",
        "password_hash": "TEXT",
        "created_at": "TEXT",
    },
    "accounts": {
        "user_id": "INTEGER",
        "name": "TEXT",
        "type": "TEXT DEFAULT 'Cash'",
        "balance": "REAL NOT NULL DEFAULT 0",
        "created_at": "TEXT",
    },
    "categories": {
        "user_id": "INTEGER",
        "name": "TEXT",
        "created_at": "TEXT",
    },
    "expenses": {
        "user_id": "INTEGER",
        "account_id": "INTEGER",
        "category": "TEXT DEFAULT 'Other'",
        "amount": "REAL NOT NULL DEFAULT 0",
        "note": "TEXT",
        "spent_on": "TEXT",
        "created_at": "TEXT",
    },
}


def get_db():
    if "db" not in g:
        os.makedirs(app.instance_path, exist_ok=True)
        g.db = sqlite3.connect(app.config["DATABASE"])
        g.db.row_factory = sqlite3.Row
        g.db.execute("PRAGMA journal_mode=MEMORY")
        g.db.execute("PRAGMA foreign_keys=ON")
    return g.db


@app.teardown_appcontext
def close_db(_error=None):
    db = g.pop("db", None)
    if db is not None:
        db.close()


def init_db():
    db = get_db()
    db.executescript(
        """
        CREATE TABLE IF NOT EXISTS users (
            id INTEGER PRIMARY KEY AUTOINCREMENT,
            username TEXT UNIQUE NOT NULL,
            password_hash TEXT NOT NULL,
            created_at TEXT NOT NULL
        );

        CREATE TABLE IF NOT EXISTS accounts (
            id INTEGER PRIMARY KEY AUTOINCREMENT,
            user_id INTEGER NOT NULL,
            name TEXT NOT NULL,
            type TEXT NOT NULL,
            balance REAL NOT NULL DEFAULT 0,
            created_at TEXT NOT NULL,
            FOREIGN KEY (user_id) REFERENCES users (id)
        );

        CREATE TABLE IF NOT EXISTS categories (
            id INTEGER PRIMARY KEY AUTOINCREMENT,
            user_id INTEGER NOT NULL,
            name TEXT NOT NULL,
            created_at TEXT NOT NULL,
            UNIQUE (user_id, name),
            FOREIGN KEY (user_id) REFERENCES users (id)
        );

        CREATE TABLE IF NOT EXISTS expenses (
            id INTEGER PRIMARY KEY AUTOINCREMENT,
            user_id INTEGER NOT NULL,
            account_id INTEGER NOT NULL,
            category TEXT NOT NULL,
            amount REAL NOT NULL,
            note TEXT,
            spent_on TEXT NOT NULL,
            created_at TEXT NOT NULL,
            FOREIGN KEY (user_id) REFERENCES users (id),
            FOREIGN KEY (account_id) REFERENCES accounts (id)
        );
        """
    )
    migrate_db(db)
    db.commit()


def migrate_db(db):
    """Add columns that may be missing from older local SQLite databases."""
    timestamp = datetime.now().isoformat(timespec="seconds")
    for table, columns in TABLE_COLUMNS.items():
        existing_columns = {
            row["name"] for row in db.execute(f"PRAGMA table_info({table})").fetchall()
        }
        for column, definition in columns.items():
            if column not in existing_columns:
                db.execute(f"ALTER TABLE {table} ADD COLUMN {column} {definition}")

    db.execute(
        "UPDATE accounts SET type = 'Cash' WHERE type IS NULL OR type = ''"
    )
    db.execute("UPDATE accounts SET balance = 0 WHERE balance IS NULL")
    db.execute(
        "UPDATE accounts SET created_at = ? WHERE created_at IS NULL OR created_at = ''",
        (timestamp,),
    )
    db.execute(
        "UPDATE expenses SET category = 'Other' WHERE category IS NULL OR category = ''"
    )
    db.execute(
        "UPDATE expenses SET spent_on = date(created_at) WHERE spent_on IS NULL OR spent_on = ''"
    )
    db.execute(
        "UPDATE expenses SET spent_on = date('now') WHERE spent_on IS NULL OR spent_on = ''"
    )
    db.execute(
        "UPDATE expenses SET created_at = ? WHERE created_at IS NULL OR created_at = ''",
        (timestamp,),
    )
    db.execute(
        "UPDATE users SET created_at = ? WHERE created_at IS NULL OR created_at = ''",
        (timestamp,),
    )
    merge_duplicate_accounts(db)


def merge_duplicate_accounts(db):
    """Combine repeated accounts created before add-account updated balances."""
    duplicate_groups = db.execute(
        """
        SELECT
            user_id,
            LOWER(TRIM(name)) AS normalized_name,
            type,
            MIN(id) AS keep_id,
            SUM(balance) AS total_balance,
            COUNT(*) AS account_count
        FROM accounts
        GROUP BY user_id, normalized_name, type
        HAVING account_count > 1
        """
    ).fetchall()

    for group in duplicate_groups:
        duplicate_accounts = db.execute(
            """
            SELECT id
            FROM accounts
            WHERE user_id = ?
              AND LOWER(TRIM(name)) = ?
              AND type = ?
              AND id != ?
            """,
            (
                group["user_id"],
                group["normalized_name"],
                group["type"],
                group["keep_id"],
            ),
        ).fetchall()
        duplicate_ids = [account["id"] for account in duplicate_accounts]

        db.execute(
            "UPDATE accounts SET balance = ? WHERE id = ?",
            (group["total_balance"], group["keep_id"]),
        )
        for duplicate_id in duplicate_ids:
            db.execute(
                "UPDATE expenses SET account_id = ? WHERE account_id = ?",
                (group["keep_id"], duplicate_id),
            )
            db.execute("DELETE FROM accounts WHERE id = ?", (duplicate_id,))


@app.cli.command("init-db")
def init_db_command():
    init_db()
    print("Initialized the expense tracker database.")


@app.before_request
def ensure_database():
    init_db()


def current_user():
    user_id = session.get("user_id")
    if not user_id:
        return None
    return get_db().execute("SELECT * FROM users WHERE id = ?", (user_id,)).fetchone()


@app.context_processor
def inject_globals():
    return {"current_user": current_user(), "low_balance_limit": LOW_BALANCE_LIMIT}


def login_required(view):
    @wraps(view)
    def wrapped_view(*args, **kwargs):
        if not session.get("user_id"):
            flash("Please login first.", "warning")
            return redirect(url_for("login"))
        return view(*args, **kwargs)

    return wrapped_view


def user_accounts(user_id):
    return get_db().execute(
        "SELECT * FROM accounts WHERE user_id = ? ORDER BY created_at DESC", (user_id,)
    ).fetchall()


def seed_default_categories(user_id):
    db = get_db()
    created_at = datetime.now().isoformat(timespec="seconds")
    for category in DEFAULT_CATEGORIES:
        db.execute(
            """
            INSERT OR IGNORE INTO categories (user_id, name, created_at)
            VALUES (?, ?, ?)
            """,
            (user_id, category, created_at),
        )
    db.commit()


def ensure_other_category(user_id):
    get_db().execute(
        """
        INSERT OR IGNORE INTO categories (user_id, name, created_at)
        VALUES (?, 'Other', ?)
        """,
        (user_id, datetime.now().isoformat(timespec="seconds")),
    )
    get_db().commit()


def user_categories(user_id):
    return get_db().execute(
        """
        SELECT categories.*
        FROM categories
        LEFT JOIN expenses
            ON expenses.user_id = categories.user_id
            AND expenses.category = categories.name
        WHERE categories.user_id = ?
        GROUP BY categories.id
        ORDER BY COUNT(expenses.id) DESC, COALESCE(SUM(expenses.amount), 0) DESC, categories.name
        """,
        (user_id,),
    ).fetchall()


def dashboard_date_filter(period, selected_date):
    if period == "calendar":
        return "AND date(spent_on) = date(?)", [selected_date.isoformat()]

    start_date = period_start(period)
    if start_date:
        return "AND date(spent_on) >= date(?)", [start_date.isoformat()]

    return "", []


def dashboard_expense_categories(user_id, period, selected_date):
    date_filter, date_params = dashboard_date_filter(period, selected_date)
    params = [user_id, *date_params]

    return [
        row["category"]
        for row in get_db()
        .execute(
            f"""
            SELECT category
            FROM expenses
            WHERE user_id = ?
            {date_filter}
            GROUP BY category
            ORDER BY MAX(spent_on) DESC, MAX(created_at) DESC, category
            """,
            params,
        )
        .fetchall()
    ]


def categories_with_usage(user_id):
    return get_db().execute(
        """
        SELECT
            categories.*,
            COUNT(expenses.id) AS expense_count,
            COALESCE(SUM(expenses.amount), 0) AS total
        FROM categories
        LEFT JOIN expenses
            ON expenses.user_id = categories.user_id
            AND expenses.category = categories.name
        WHERE categories.user_id = ?
        GROUP BY categories.id
        ORDER BY expense_count DESC, total DESC, categories.name
        """,
        (user_id,),
    ).fetchall()


def period_start(period):
    today = date.today()
    if period == "daily":
        return today - timedelta(days=6)
    if period == "weekly":
        return today - timedelta(weeks=7)
    if period == "monthly":
        return today.replace(month=1, day=1)
    if period == "yearly":
        return None
    return None


def period_options():
    return [
        ("calendar", "Calendar date"),
        ("daily", "Daily"),
        ("weekly", "Weekly"),
        ("monthly", "Monthly"),
        ("yearly", "Yearly"),
    ]


def trend_group(period):
    if period == "weekly":
        return "strftime('%Y-W%W', spent_on)"
    if period == "monthly":
        return "strftime('%Y-%m', spent_on)"
    if period == "yearly":
        return "strftime('%Y', spent_on)"
    return "date(spent_on)"


def format_trend_label(period, label):
    if period != "weekly":
        return label

    try:
        year_text, week_text = label.split("-W", 1)
        week_start = datetime.strptime(
            f"{year_text}-{week_text}-1", "%Y-%W-%w"
        ).date()
    except (TypeError, ValueError):
        return label

    week_end = week_start + timedelta(days=6)
    return f"{week_start.strftime('%d-%m-%y')} to {week_end.strftime('%d-%m-%y')}"


def parse_iso_date(value):
    try:
        return datetime.strptime(value, "%Y-%m-%d").date()
    except (TypeError, ValueError):
        return None


def current_week_start(today=None):
    today = today or date.today()
    return today - timedelta(days=today.weekday())


def format_week_range(week_start):
    week_end = week_start + timedelta(days=6)
    return f"{week_start.strftime('%d-%m-%y')} to {week_end.strftime('%d-%m-%y')}"


def expense_period_summary(period, selected_date):
    if period == "daily":
        return selected_date.strftime("%d-%m-%y")
    if period == "weekly":
        return format_week_range(current_week_start(selected_date))
    if period == "monthly":
        return selected_date.strftime("%b %Y")
    if period == "yearly":
        return selected_date.strftime("%Y")
    return "All saved expenses"


def expense_date_summary(period, selected_date, week_start, week_end, selected_month, selected_year):
    if period == "daily":
        return selected_date.strftime("%d-%m-%y")
    if period == "weekly":
        return f"{week_start.strftime('%d-%m-%y')} to {week_end.strftime('%d-%m-%y')}"
    if period == "monthly":
        try:
            return datetime.strptime(selected_month, "%Y-%m").strftime("%b %Y")
        except (TypeError, ValueError):
            return selected_date.strftime("%b %Y")
    if period == "yearly":
        return selected_year
    return "All saved expenses"


def expense_period_choices(user_id):
    rows = get_db().execute(
        """
        SELECT spent_on
        FROM expenses
        WHERE user_id = ?
          AND spent_on IS NOT NULL
          AND spent_on != ''
        ORDER BY date(spent_on) DESC
        """,
        (user_id,),
    ).fetchall()
    today = date.today()
    dates = [parse_iso_date(row["spent_on"]) for row in rows]
    dates = [spent_on for spent_on in dates if spent_on]
    min_date = min(dates, default=today)
    max_date = max([today, *dates])

    daily_options = [
        (spent_on.isoformat(), spent_on.strftime("%d-%m-%y"))
        for spent_on in sorted(set(dates + [today]), reverse=True)
    ]

    month_values = sorted(
        {spent_on.strftime("%Y-%m") for spent_on in dates + [today]},
        reverse=True,
    )
    monthly_options = [
        (
            month_value,
            datetime.strptime(month_value, "%Y-%m").strftime("%b %Y"),
        )
        for month_value in month_values
    ]

    year_values = sorted(
        {spent_on.strftime("%Y") for spent_on in dates + [today]},
        reverse=True,
    )
    yearly_options = [(year_value, year_value) for year_value in year_values]

    first_week = current_week_start(min_date)
    last_week = current_week_start(max_date)
    weekly_options = []
    week_start = last_week
    while week_start >= first_week:
        weekly_options.append((week_start.isoformat(), format_week_range(week_start)))
        week_start -= timedelta(days=7)

    return {
        "daily": daily_options,
        "weekly": weekly_options,
        "monthly": monthly_options,
        "yearly": yearly_options,
    }


def dashboard_stats(user_id, period="monthly", recent_category="", selected_date=None):
    db = get_db()
    accounts = user_accounts(user_id)
    selected_date = selected_date or date.today()
    trend_bucket = trend_group(period)
    date_filter, date_params = dashboard_date_filter(period, selected_date)
    params = [user_id, *date_params]

    recent_filter = date_filter
    recent_params = list(params)
    if recent_category:
        recent_filter = f"{recent_filter} AND expenses.category = ?"
        recent_params.append(recent_category)

    expense_limit = "" if period == "calendar" else "LIMIT 8"
    expenses = db.execute(
        f"""
        SELECT expenses.*, accounts.name AS account_name
        FROM expenses
        JOIN accounts ON accounts.id = expenses.account_id
        WHERE expenses.user_id = ?
        {recent_filter}
        ORDER BY expenses.spent_on DESC, expenses.created_at DESC
        {expense_limit}
        """,
        recent_params,
    ).fetchall()
    category_rows = db.execute(
        f"""
        SELECT category, COUNT(id) AS expense_count, SUM(amount) AS total
        FROM expenses
        WHERE user_id = ?
        {date_filter}
        GROUP BY category
        ORDER BY total DESC
        """,
        params,
    ).fetchall()
    trend_rows = db.execute(
        f"""
        SELECT {trend_bucket} AS period_label, SUM(amount) AS total
        FROM expenses
        WHERE user_id = ?
        {date_filter}
        GROUP BY period_label
        ORDER BY period_label
        """,
        params,
    ).fetchall()
    total_balance = sum(account["balance"] for account in accounts)
    total_spent = sum(row["total"] for row in category_rows)
    transaction_count = sum(row["expense_count"] for row in category_rows)
    low_accounts = [
        {
            **dict(account),
            "alert_name": account_alert_name(account),
        }
        for account in accounts
        if account["balance"] < LOW_BALANCE_LIMIT
    ]
    return (
        accounts,
        expenses,
        category_rows,
        trend_rows,
        total_balance,
        total_spent,
        low_accounts,
        transaction_count,
    )


def account_alert_name(account):
    name = (account["name"] or "").strip()
    account_type = (account["type"] or "").strip()
    if account_type and account_type != "Card":
        return account_type
    if not name:
        return account_type or "Account"
    if not account_type or account_type.lower() in name.lower():
        return name
    return f"{name} {account_type}"


@app.route("/")
def index():
    if session.get("user_id"):
        return redirect(url_for("dashboard"))
    return render_template("home.html")


@app.route("/get-started")
def get_started():
    if session.get("user_id"):
        return redirect(url_for("dashboard"))
    return render_template("get_started.html")


@app.route("/register", methods=["GET", "POST"])
def register():
    if request.method == "POST":
        username = request.form.get("username", "").strip()
        password = request.form.get("password", "")
        confirm_password = request.form.get("confirm_password", "")

        if not username or not password:
            flash("Username and password are required.", "danger")
        elif len(password) < 6:
            flash("Password must be at least 6 characters.", "danger")
        elif password != confirm_password:
            flash("Passwords do not match.", "danger")
        else:
            try:
                db = get_db()
                db.execute(
                    """
                    INSERT INTO users (username, password_hash, created_at)
                    VALUES (?, ?, ?)
                    """,
                    (
                        username,
                        generate_password_hash(password),
                        datetime.now().isoformat(timespec="seconds"),
                    ),
                )
                db.commit()
                user = db.execute(
                    "SELECT id FROM users WHERE username = ?", (username,)
                ).fetchone()
                seed_default_categories(user["id"])
                flash("Registration successful. Please login.", "success")
                return redirect(url_for("login"))
            except sqlite3.IntegrityError:
                flash("That username is already registered.", "danger")

    return render_template("register.html")


@app.route("/login", methods=["GET", "POST"])
def login():
    if request.method == "POST":
        username = request.form.get("username", "").strip()
        password = request.form.get("password", "")
        user = get_db().execute(
            "SELECT * FROM users WHERE username = ?", (username,)
        ).fetchone()

        if user and check_password_hash(user["password_hash"], password):
            session.clear()
            session["user_id"] = user["id"]
            session["username"] = user["username"]
            flash("Welcome back.", "success")
            return redirect(url_for("dashboard"))
        flash("Invalid username or password.", "danger")

    return render_template("login.html")


@app.route("/logout")
def logout():
    session.clear()
    flash("You have been logged out.", "info")
    return redirect(url_for("login"))


def save_expense(user_id, category_rows):
    account_id = request.form.get("account_id")
    category = request.form.get("category", "")
    amount = request.form.get("amount", "0")
    note = request.form.get("note", "").strip()
    spent_on = request.form.get("spent_on") or datetime.now().date().isoformat()

    try:
        amount_value = float(amount)
    except ValueError:
        amount_value = -1

    account = get_db().execute(
        "SELECT * FROM accounts WHERE id = ? AND user_id = ?",
        (account_id, user_id),
    ).fetchone()

    if not account:
        flash("Choose one of your accounts.", "danger")
    elif category not in [row["name"] for row in category_rows]:
        flash("Choose a valid category.", "danger")
    elif amount_value <= 0:
        flash("Expense amount must be greater than zero.", "danger")
    elif account["balance"] < amount_value:
        flash("This expense is greater than the selected account balance.", "danger")
    else:
        db = get_db()
        db.execute(
            """
            INSERT INTO expenses
                (user_id, account_id, category, amount, note, spent_on, created_at)
            VALUES (?, ?, ?, ?, ?, ?, ?)
            """,
            (
                user_id,
                account["id"],
                category,
                amount_value,
                note,
                spent_on,
                datetime.now().isoformat(timespec="seconds"),
            ),
        )
        db.execute(
            "UPDATE accounts SET balance = balance - ? WHERE id = ?",
            (amount_value, account["id"]),
        )
        db.commit()
        updated_balance = account["balance"] - amount_value
        if updated_balance < LOW_BALANCE_LIMIT:
            flash(
                f"Expense saved. Alert: {account['name']} balance is below 1000.",
                "danger",
            )
        else:
            flash("Expense saved successfully.", "success")
        return True

    return False


@app.route("/dashboard", methods=["GET", "POST"])
@login_required
def dashboard():
    period = request.args.get("period", "monthly")
    allowed_periods = [value for value, _label in period_options()]
    if period not in allowed_periods:
        period = "monthly"
    selected_date = parse_iso_date(request.args.get("selected_date")) or date.today()

    # Load all user categories (ordered by usage) and also determine which
    # categories appear in expenses for the current period. Build the recent
    # category dropdown to include all saved categories while keeping the
    # preferred dashboard ordering at the front.
    categories = user_categories(session["user_id"])
    if request.method == "POST" and save_expense(session["user_id"], categories):
        redirect_args = {
            "period": period,
            "selected_date": selected_date.isoformat(),
        }
        recent_category_arg = request.args.get("recent_category", "")
        if recent_category_arg:
            redirect_args["recent_category"] = recent_category_arg
        return redirect(url_for("dashboard", **redirect_args))

    saved_category_names = [c["name"] for c in categories]
    expense_category_names = dashboard_expense_categories(
        session["user_id"], period, selected_date
    )

    # Build the dropdown from categories the user has saved or actually used in expenses.
    # Keep the preferred dashboard order for known category names first.
    recent_category_options = []
    seen = set()

    for name in DASHBOARD_CATEGORY_ORDER:
        if name in saved_category_names or name in expense_category_names:
            recent_category_options.append(name)
            seen.add(name)

    for name in expense_category_names:
        if name not in seen:
            recent_category_options.append(name)
            seen.add(name)

    for name in saved_category_names:
        if name not in seen:
            recent_category_options.append(name)
            seen.add(name)

    recent_category = request.args.get("recent_category", "")
    all_category_names = set(saved_category_names + expense_category_names)
    # Only accept recent_category values that exist in the user's saved categories
    if recent_category and recent_category not in all_category_names:
        recent_category = ""

    (
        accounts,
        expenses,
        category_rows,
        trend_rows,
        total_balance,
        total_spent,
        low_accounts,
        transaction_count,
    ) = (
        dashboard_stats(session["user_id"], period, recent_category, selected_date)
    )
    return render_template(
        "dashboard.html",
        accounts=accounts,
        expenses=expenses,
        categories=categories,
        recent_category_options=recent_category_options,
        recent_category=recent_category,
        category_rows=category_rows,
        total_balance=total_balance,
        total_spent=total_spent,
        low_accounts=low_accounts,
        transaction_count=transaction_count,
        period=period,
        selected_date=selected_date.isoformat(),
        selected_date_label=selected_date.strftime("%d-%m-%Y"),
        today=date.today().isoformat(),
        period_options=period_options(),
        chart_labels=[row["category"] for row in category_rows],
        chart_values=[round(row["total"], 2) for row in category_rows],
        trend_labels=[
            format_trend_label(period, row["period_label"]) for row in trend_rows
        ],
        trend_values=[round(row["total"], 2) for row in trend_rows],
    )


@app.route("/accounts", methods=["GET", "POST"])
@login_required
def accounts():
    if request.method == "POST":
        account_mode = request.form.get("account_mode", "account")
        cash_balance = request.form.get("cash_balance", "").strip()
        account_name = request.form.get("name", "").strip()

        if account_mode == "cash_in_hand" or (cash_balance and not account_name):
            name = "Cash in Hand"
            account_type = "Cash in Hand"
            balance = cash_balance or "0"
        else:
            name = account_name
            account_type = request.form.get("type", "")
            balance = request.form.get("balance", "0")

        try:
            balance_value = float(balance)
        except ValueError:
            balance_value = -1

        if not name:
            flash("Account name is required.", "danger")
        elif account_type not in ACCOUNT_TYPES:
            flash("Choose a valid account type.", "danger")
        elif balance_value < 0:
            flash("Balance must be zero or more.", "danger")
        else:
            db = get_db()
            existing_account = db.execute(
                """
                SELECT id, balance FROM accounts
                WHERE user_id = ?
                  AND LOWER(TRIM(name)) = LOWER(TRIM(?))
                  AND type = ?
                """,
                (session["user_id"], name, account_type),
            ).fetchone()

            if existing_account:
                updated_balance = existing_account["balance"] + balance_value
                db.execute(
                    "UPDATE accounts SET balance = ? WHERE id = ?",
                    (updated_balance, existing_account["id"]),
                )
                flash(
                    f"Added successfully. Available balance: Rs. {updated_balance:.2f}.",
                    "success",
                )
            else:
                db.execute(
                    """
                    INSERT INTO accounts (user_id, name, type, balance, created_at)
                    VALUES (?, ?, ?, ?, ?)
                    """,
                    (
                        session["user_id"],
                        name,
                        account_type,
                        balance_value,
                        datetime.now().isoformat(timespec="seconds"),
                    ),
                )
                flash(
                    f"Added successfully. Available balance: Rs. {balance_value:.2f}.",
                    "success",
                )
            db.commit()
            return redirect(url_for("accounts"))

    return render_template(
        "accounts.html",
        accounts=user_accounts(session["user_id"]),
        account_types=ACCOUNT_TYPES,
        account_name_types=[type for type in ACCOUNT_TYPES if type != "Cash in Hand"],
    )


@app.route("/accounts/<int:account_id>/update", methods=["POST"])
@login_required
def update_account(account_id):
    name = request.form.get("name", "").strip()
    account_type = request.form.get("type", "")
    balance = request.form.get("balance", "0")
    account = get_db().execute(
        "SELECT * FROM accounts WHERE id = ? AND user_id = ?",
        (account_id, session["user_id"]),
    ).fetchone()

    try:
        balance_value = float(balance)
    except ValueError:
        balance_value = -1

    if not account:
        flash("Account not found.", "danger")
    elif not name:
        flash("Account name is required.", "danger")
    elif account_type not in ACCOUNT_TYPES:
        flash("Choose a valid account type.", "danger")
    elif balance_value < 0:
        flash("Balance must be zero or more.", "danger")
    else:
        get_db().execute(
            """
            UPDATE accounts
            SET name = ?, type = ?, balance = ?
            WHERE id = ? AND user_id = ?
            """,
            (name, account_type, balance_value, account_id, session["user_id"]),
        )
        get_db().commit()
        flash("Account updated successfully.", "success")

    return redirect(url_for("accounts"))


@app.route("/accounts/<int:account_id>/delete", methods=["POST"])
@login_required
def delete_account(account_id):
    account = get_db().execute(
        "SELECT * FROM accounts WHERE id = ? AND user_id = ?",
        (account_id, session["user_id"]),
    ).fetchone()

    if not account:
        flash("Account not found.", "danger")
    else:
        expense_count = get_db().execute(
            "SELECT COUNT(*) AS count FROM expenses WHERE user_id = ? AND account_id = ?",
            (session["user_id"], account_id),
        ).fetchone()["count"]
        if expense_count:
            flash("You cannot delete an account that has expenses.", "danger")
        else:
            get_db().execute(
                "DELETE FROM accounts WHERE id = ? AND user_id = ?",
                (account_id, session["user_id"]),
            )
            get_db().commit()
            flash("Account deleted successfully.", "success")

    return redirect(url_for("accounts"))


@app.route("/categories", methods=["GET", "POST"])
@login_required
def categories():
    if request.method == "POST":
        name = request.form.get("name", "").strip()
        if not name:
            flash("Category name is required.", "danger")
        else:
            try:
                get_db().execute(
                    """
                    INSERT INTO categories (user_id, name, created_at)
                    VALUES (?, ?, ?)
                    """,
                    (
                        session["user_id"],
                        name,
                        datetime.now().isoformat(timespec="seconds"),
                    ),
                )
                get_db().commit()
                flash("Category added successfully.", "success")
                return redirect(url_for("categories"))
            except sqlite3.IntegrityError:
                flash("This category already exists.", "danger")

    return render_template(
        "categories.html",
        categories=categories_with_usage(session["user_id"]),
    )


@app.route("/categories/<int:category_id>/update", methods=["POST"])
@login_required
def update_category(category_id):
    new_name = request.form.get("name", "").strip()
    category = get_db().execute(
        "SELECT * FROM categories WHERE id = ? AND user_id = ?",
        (category_id, session["user_id"]),
    ).fetchone()

    if not category:
        flash("Category not found.", "danger")
    elif not new_name:
        flash("Category name is required.", "danger")
    else:
        try:
            db = get_db()
            db.execute(
                "UPDATE categories SET name = ? WHERE id = ? AND user_id = ?",
                (new_name, category_id, session["user_id"]),
            )
            db.execute(
                "UPDATE expenses SET category = ? WHERE user_id = ? AND category = ?",
                (new_name, session["user_id"], category["name"]),
            )
            db.commit()
            flash("Category updated successfully.", "success")
        except sqlite3.IntegrityError:
            flash("This category already exists.", "danger")

    return redirect(url_for("categories"))


@app.route("/categories/<int:category_id>/delete", methods=["POST"])
@login_required
def delete_category(category_id):
    category = get_db().execute(
        "SELECT * FROM categories WHERE id = ? AND user_id = ?",
        (category_id, session["user_id"]),
    ).fetchone()

    if not category:
        flash("Category not found.", "danger")
    elif category["name"] == "Other":
        flash("The Other category is required and cannot be deleted.", "danger")
    else:
        ensure_other_category(session["user_id"])
        db = get_db()
        db.execute(
            """
            UPDATE expenses
            SET category = 'Other'
            WHERE user_id = ? AND category = ?
            """,
            (session["user_id"], category["name"]),
        )
        db.execute(
            "DELETE FROM categories WHERE id = ? AND user_id = ?",
            (category_id, session["user_id"]),
        )
        db.commit()
        flash(
            "Category deleted successfully. Existing expenses were moved to Other.",
            "success",
        )

    return redirect(url_for("categories"))


@app.route("/expenses", methods=["GET", "POST"])
@login_required
def expenses():
    accounts_for_user = user_accounts(session["user_id"])
    category_rows = user_categories(session["user_id"])
    if request.method == "POST" and save_expense(session["user_id"], category_rows):
        return redirect(url_for("expenses"))

    period = request.args.get("period", "all")
    category = request.args.get("category", "")
    allowed_periods = ["all", "daily", "weekly", "monthly", "yearly"]
    if period not in allowed_periods:
        period = "all"
    filter_by = "date" if period != "all" else "category"

    all_category_names = [row["name"] for row in category_rows]
    if category not in all_category_names:
        category = ""

    today = date.today()
    selected_date = parse_iso_date(request.args.get("selected_date")) or today
    week_start = parse_iso_date(request.args.get("date_from")) or current_week_start(selected_date)
    week_end = parse_iso_date(request.args.get("date_to")) or (week_start + timedelta(days=6))
    if week_end < week_start:
        week_start, week_end = week_end, week_start
    selected_month = request.args.get("selected_month") or selected_date.strftime("%Y-%m")
    try:
        datetime.strptime(selected_month, "%Y-%m")
    except ValueError:
        selected_month = selected_date.strftime("%Y-%m")
    selected_year = request.args.get("selected_year") or selected_date.strftime("%Y")
    if not selected_year.isdigit() or len(selected_year) != 4:
        selected_year = selected_date.strftime("%Y")
    period_summary = expense_date_summary(
        period,
        selected_date,
        week_start,
        week_end,
        selected_month,
        selected_year,
    )
    if category:
        period_summary = f"{category} - {period_summary}"

    query = [
        "SELECT",
        "    expenses.*,",
        "    COALESCE(accounts.name, 'Account removed') AS account_name",
        "FROM expenses",
        "LEFT JOIN accounts",
        "    ON accounts.id = expenses.account_id",
        "    AND accounts.user_id = expenses.user_id",
        "WHERE expenses.user_id = ?",
    ]
    params = [session["user_id"]]

    if category:
        query.append("AND expenses.category = ?")
        params.append(category)

    if period == "daily":
        query.append("AND date(expenses.spent_on) = date(?)")
        params.append(selected_date.isoformat())
    elif period == "weekly":
        query.append("AND date(expenses.spent_on) BETWEEN date(?) AND date(?)")
        params.extend([week_start.isoformat(), week_end.isoformat()])
    elif period == "monthly":
        query.append("AND strftime('%Y-%m', expenses.spent_on) = ?")
        params.append(selected_month)
    elif period == "yearly":
        query.append("AND strftime('%Y', expenses.spent_on) = ?")
        params.append(selected_year)

    query.append("ORDER BY expenses.spent_on DESC, expenses.created_at DESC")

    expense_rows = get_db().execute("\n".join(query), params).fetchall()
    expense_total = sum(row["amount"] for row in expense_rows)
    empty_expense_message = f"No details found for {period_summary}."
    return render_template(
        "expenses.html",
        accounts=accounts_for_user,
        expenses=expense_rows,
        expense_total=expense_total,
        empty_expense_message=empty_expense_message,
        categories=category_rows,
        today=datetime.now().date().isoformat(),
        period=period,
        period_summary=period_summary,
        active_category=category,
        filter_by=filter_by,
        selected_date=selected_date.isoformat(),
        date_from=week_start.isoformat(),
        date_to=week_end.isoformat(),
        selected_month=selected_month,
        selected_year=selected_year,
    )


@app.route("/expenses/<int:expense_id>/edit", methods=["GET", "POST"])
@login_required
def edit_expense(expense_id):
    db = get_db()
    expense = db.execute(
        "SELECT * FROM expenses WHERE id = ? AND user_id = ?",
        (expense_id, session["user_id"]),
    ).fetchone()

    if not expense:
        flash("Expense not found.", "danger")
        return redirect(url_for("expenses"))

    accounts_for_user = user_accounts(session["user_id"])
    category_rows = user_categories(session["user_id"])

    if request.method == "GET":
        return render_template(
            "edit_expense.html",
            expense=expense,
            accounts=accounts_for_user,
            categories=category_rows,
        )

    account_id = request.form.get("account_id")
    category = request.form.get("category", "")
    amount = request.form.get("amount", "0")
    note = request.form.get("note", "").strip()
    spent_on = request.form.get("spent_on") or datetime.now().date().isoformat()

    try:
        amount_value = float(amount)
    except ValueError:
        amount_value = -1

    account = db.execute(
        "SELECT * FROM accounts WHERE id = ? AND user_id = ?",
        (account_id, session["user_id"]),
    ).fetchone()
    category_names = [row["name"] for row in category_rows]

    if not account:
        flash("Choose one of your accounts.", "danger")
    elif category not in category_names:
        flash("Choose a valid category.", "danger")
    elif amount_value <= 0:
        flash("Expense amount must be greater than zero.", "danger")
    else:
        old_account = db.execute(
            "SELECT * FROM accounts WHERE id = ? AND user_id = ?",
            (expense["account_id"], session["user_id"]),
        ).fetchone()
        if old_account["id"] == account["id"]:
            available_balance = account["balance"] + expense["amount"]
            if available_balance < amount_value:
                flash(
                    "This updated expense is greater than the selected account balance.",
                    "danger",
                )
                return redirect(url_for("expenses"))
            db.execute(
                "UPDATE accounts SET balance = ? WHERE id = ? AND user_id = ?",
                (
                    available_balance - amount_value,
                    account["id"],
                    session["user_id"],
                ),
            )
        else:
            if account["balance"] < amount_value:
                flash(
                    "This updated expense is greater than the selected account balance.",
                    "danger",
                )
                return redirect(url_for("expenses"))
            db.execute(
                "UPDATE accounts SET balance = balance + ? WHERE id = ? AND user_id = ?",
                (expense["amount"], old_account["id"], session["user_id"]),
            )
            db.execute(
                "UPDATE accounts SET balance = balance - ? WHERE id = ? AND user_id = ?",
                (amount_value, account["id"], session["user_id"]),
            )

        db.execute(
            """
            UPDATE expenses
            SET account_id = ?, category = ?, amount = ?, note = ?, spent_on = ?
            WHERE id = ? AND user_id = ?
            """,
            (
                account["id"],
                category,
                amount_value,
                note,
                spent_on,
                expense_id,
                session["user_id"],
            ),
        )
        db.commit()
        flash("Expense updated successfully.", "success")
        return redirect(url_for("expenses"))

    return render_template(
        "edit_expense.html",
        expense=expense,
        accounts=accounts_for_user,
        categories=category_rows,
    )


@app.route("/expenses/<int:expense_id>/update", methods=["POST"])
@login_required
def update_expense(expense_id):
    return edit_expense(expense_id)



@app.route("/expenses/<int:expense_id>/delete", methods=["POST"])
@login_required
def delete_expense(expense_id):
    db = get_db()
    expense = db.execute(
        "SELECT * FROM expenses WHERE id = ? AND user_id = ?",
        (expense_id, session["user_id"]),
    ).fetchone()

    if not expense:
        flash("Expense not found.", "danger")
    else:
        db.execute(
            "UPDATE accounts SET balance = balance + ? WHERE id = ? AND user_id = ?",
            (expense["amount"], expense["account_id"], session["user_id"]),
        )
        db.execute(
            "DELETE FROM expenses WHERE id = ? AND user_id = ?",
            (expense_id, session["user_id"]),
        )
        db.commit()
        flash("Expense deleted successfully. The amount was added back to the account.", "success")

    return redirect(url_for("expenses"))


if __name__ == "__main__":
    app.run(debug=True, use_reloader=False)
