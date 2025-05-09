from flask import Flask, render_template, request, redirect, url_for, session, flash, send_from_directory, g
import sqlite3
from datetime import datetime
from werkzeug.utils import secure_filename
import os

app = Flask(__name__)

# 파일 업로드 설정
UPLOAD_FOLDER = 'uploads'  # 파일을 저장할 폴더
if not os.path.exists(UPLOAD_FOLDER):
    os.makedirs(UPLOAD_FOLDER)
ALLOWED_EXTENSIONS = {'png', 'jpg', 'jpeg', 'gif', 'pdf', 'zip'}  # 허용할 파일 확장자

app.config['UPLOAD_FOLDER'] = UPLOAD_FOLDER
app.config['SECRET_KEY'] = 'your_secret_key'

# 허용된 파일 확장자인지 확인하는 함수
def allowed_file(filename):
    return '.' in filename and filename.rsplit('.', 1)[1].lower() in ALLOWED_EXTENSIONS

# 데이터베이스 설정
DATABASE = "bulletin_board.db"
app.secret_key = 'your_secret_key'
app.config['UPLOADED_FILES_DEST'] = 'uploads'

def get_db():
    db = getattr(g, '_database', None)
    if db is None:
        db = g._database = sqlite3.connect(DATABASE)
        db.row_factory = sqlite3.Row
    return db

@app.teardown_appcontext
def close_connection(exception):
    db = getattr(g, '_database', None)
    if db is not None:
        db.close()

def query_db(query, args=(), one=False):
    with app.app_context():
        cur = get_db().execute(query, args)
        rv = cur.fetchall()
        cur.close()
        return (rv[0] if rv else None) if one else rv

def execute_db(query, args=()):
    with app.app_context():
        conn = get_db()
        cur = conn.cursor()
        cur.execute(query, args)
        conn.commit()
        cur.close()

def init_db():
    with app.app_context():
        db = get_db()
        with app.open_resource('schema.sql', mode='r') as f:
            db.cursor().executescript(f.read())
        db.commit()

@app.cli.command('initdb')
def initdb_command():
    """데이터베이스를 초기화합니다."""
    init_db()
    print('데이터베이스가 초기화되었습니다.')

from auth import bp as auth_bp, get_user
app.register_blueprint(auth_bp)

@app.route("/")
def index():
    query = request.args.get('query', '')  # GET 방식으로 전달된 검색어
    user = get_user()
    if query:
        posts = query_db(
            "SELECT id, title, content, author, date_created FROM bulletin_board WHERE title LIKE ? ORDER BY date_created DESC",
            ('%' + query + '%',)
        )
    else:
        posts = query_db(
            "SELECT id, title, content, author, date_created FROM bulletin_board ORDER BY date_created DESC"
        )
    new_posts = []
    for post in posts:
        post_dict = dict(post)
        if post_dict['date_created']:
            post_dict['date_created'] = datetime.strptime(post_dict['date_created'], '%Y-%m-%d %H:%M:%S')
        new_posts.append(post_dict)
    return render_template("index.html", posts=new_posts, user=user, query=query)
    
@app.route("/create", methods=["GET", "POST"])
def create():
    user = get_user()  # 현재 로그인된 유저 정보 가져오기
    if not user:
        flash("로그인 후 게시글을 작성할 수 있습니다.")
        return redirect(url_for("auth.login"))

    if request.method == "POST":
        title = request.form["title"]
        content = request.form["content"]
        author = user['username']

        # 파일이 첨부된 경우
        if 'file' in request.files:
            file = request.files['file']
            if file and allowed_file(file.filename):  # 허용된 파일인지 확인
                filename = secure_filename(file.filename)  # 파일명 안전하게 변경
                file_path = os.path.join(app.config['UPLOAD_FOLDER'], filename)  # 파일 저장 경로
                file.save(file_path)  # 파일 저장
            else:
                file_path = None
        else:
            file_path = None

        # 게시글 데이터베이스에 저장 (파일 경로 포함)
        query = f"INSERT INTO bulletin_board (title, content, author, file_path) VALUES ('{title}', '{content}', '{author}', '{file_path}')"
        execute_db(query)

        flash("게시글이 등록되었습니다!")
        return redirect(url_for("index"))
   
    return render_template("create_post.html")

@app.route("/edit/<int:id>", methods=["GET", "POST"])
def edit_post(id):
    user = get_user()
    post = query_db("SELECT id, title, content, author FROM bulletin_board WHERE id = ?", (id,), one=True)

    if not user or post['author'] != user['username']:
        flash("게시글 수정 권한이 없습니다.")
        return redirect(url_for("view_post", post_id=id))

    if request.method == "POST":
        title = request.form["title"]
        content = request.form["content"]
        query = f"UPDATE bulletin_board SET title='{title}', content='{content}' WHERE id={id}"
        execute_db(query)
        return redirect(url_for("view_post", post_id=id))

    return render_template("edit.html", post=post)

@app.route("/delete/<int:id>", methods=["POST"])
def delete_post(id):
    user = get_user()
    post = query_db("SELECT id, author, file_path FROM bulletin_board WHERE id = ?", (id,), one=True)

    if not user or post['author'] != user['username']:
        flash("게시글 삭제 권한이 없습니다.")
        return redirect(url_for("view_post", post_id=id))

    if post:
        if post['file_path']:
            try:
                os.remove(os.path.join(app.config['UPLOADED_FILES_DEST'], post['file_path']))
            except FileNotFoundError:
                pass
        execute_db(f"DELETE FROM bulletin_board WHERE id={id}")

    return redirect(url_for("index"))

@app.route("/post/<int:post_id>")
def view_post(post_id):
    post = query_db("SELECT id, title, content, author, date_created, file_path FROM bulletin_board WHERE id = ?", (post_id,), one=True)
    comments = query_db("SELECT id, content, author, date_created, parent_id FROM comment WHERE post_id = ? AND parent_id IS NULL", (post_id,))
    user = get_user()
    return render_template("post.html", post=post, comments=comments, user=user)

@app.route("/comment", methods=["POST"])
def add_comment():
    user = get_user()
    if not user:
        flash("로그인 후 댓글을 작성할 수 있습니다.")
        return redirect(request.referrer)

    content = request.form['content']
    post_id = request.form['post_id']
    parent_id = request.form.get('parent_id')

    if parent_id:
        query = f"INSERT INTO comment (content, post_id, author, parent_id) VALUES ('{content}', {post_id}, '{user['username']}', {parent_id})"
    else:
        query = f"INSERT INTO comment (content, post_id, author) VALUES ('{content}', {post_id}, '{user['username']}')"
    execute_db(query)
    return redirect(url_for('view_post', post_id=post_id))

@app.route("/edit_comment/<int:comment_id>", methods=["GET", "POST"])
def edit_comment(comment_id):
    user = get_user()
    comment = query_db("SELECT id, content, author, post_id FROM comment WHERE id = ?", (comment_id,), one=True)

    if not user or comment['author'] != user['username']:
        flash("댓글 수정 권한이 없습니다.")
        return redirect(url_for('view_post', post_id=comment['post_id']))

    if request.method == "POST":
        content = request.form["content"]
        query = f"UPDATE comment SET content='{content}' WHERE id={comment_id}"
        execute_db(query)
        return redirect(url_for('view_post', post_id=comment['post_id']))

    return redirect(url_for('view_post', post_id=comment['post_id']))

@app.route("/delete_comment/<int:comment_id>", methods=["POST"])
def delete_comment(comment_id):
    user = get_user()
    comment = query_db("SELECT id, author, post_id FROM comment WHERE id = ?", (comment_id,), one=True)
    replies = query_db("SELECT id FROM comment WHERE parent_id = ?", (comment_id,))

    if not user or comment['author'] != user['username']:
        flash("댓글 삭제 권한이 없습니다.")
        return redirect(url_for('view_post', post_id=comment['post_id']))

    if comment:
        if replies:
            execute_db(f"UPDATE comment SET content='삭제된 댓글입니다.' WHERE id={comment_id}")
        else:
            execute_db(f"DELETE FROM comment WHERE id={comment_id}")

    return redirect(url_for('view_post', post_id=comment['post_id']))

@app.route('/uploads/<filename>')
def uploaded_file(filename):
    # 'uploads' 디렉토리에서 파일을 반환
    return send_from_directory(os.path.join(app.root_path, 'uploads'), filename)

@app.route("/download/<filename>")
def download_image(filename):
    return render_template("download.html", filename=filename)

if __name__ == "__main__":
    from flask import g
    app.run(debug=True)