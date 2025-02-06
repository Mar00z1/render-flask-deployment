import os
import re
from datetime import datetime

import openai
from flask import (
    Flask,
    render_template,
    request,
    jsonify,
    make_response,
    redirect,
    url_for,
    flash
)
from flask_sqlalchemy import SQLAlchemy
from flask_migrate import Migrate
from flask_login import (
    LoginManager,
    UserMixin,
    login_user,
    logout_user,
    login_required,
    current_user
)
from werkzeug.security import generate_password_hash, check_password_hash
from werkzeug.utils import secure_filename  # Para manejo seguro de nombres de archivo

import PyPDF2  # Para extraer texto de PDFs
from docx import Document  # Para extraer texto de archivos DOCX

# Configuración de la clave API de OpenAI
openai.api_key = os.environ.get("OPENAI_API_KEY")

# Inicialización de Flask y configuración
app = Flask(__name__)
app.config['SECRET_KEY'] = os.environ.get("SECRET_KEY", "este-es-un-secreto")

# Configuración de la base de datos
database_url = os.environ.get("DATABASE_URL", "sqlite:///conversations.db").replace("postgres://", "postgresql://", 1)
app.config['SQLALCHEMY_DATABASE_URI'] = database_url
app.config['SQLALCHEMY_TRACK_MODIFICATIONS'] = False
db = SQLAlchemy(app)
migrate = Migrate(app, db)

# Configuración de Flask-Login
login_manager = LoginManager()
login_manager.init_app(app)
login_manager.login_view = 'login'

##############################################
# Modelos de Base de Datos                   #
##############################################

class User(db.Model, UserMixin):
    __tablename__ = 'users'
    id = db.Column(db.Integer, primary_key=True)
    username = db.Column(db.String(150), unique=True, nullable=False)
    password = db.Column(db.String(255), nullable=False)  # Hash de la contraseña
    memory_mode = db.Column(db.Boolean, default=False)  # Nuevo campo para modo memoria
    messages = db.relationship('Message', backref='user', lazy=True)

class Message(db.Model):
    id = db.Column(db.Integer, primary_key=True)
    role = db.Column(db.String(50), nullable=False)
    content = db.Column(db.Text, nullable=False)
    timestamp = db.Column(db.DateTime, default=datetime.utcnow)
    user_id = db.Column(db.Integer, db.ForeignKey('users.id'), nullable=False)

##############################################
# Configuración de Flask-Login               #
##############################################

@login_manager.user_loader
def load_user(user_id):
    return User.query.get(int(user_id))

##############################################
# Constantes y Configuraciones               #
##############################################

COMANDOS_VALIDOS = ['/clear', '/example', '/exercise', '/summary', '/help', '/explicar']
system_prompt_content = (
    "Eres un profesor experto que adapta explicaciones usando ejemplos prácticos y preguntas interactivas. "
    "Usa formato Markdown para:\n"
    "- Encabezados (###)\n"
    "- **Negritas** para términos clave\n"
    "- *Cursivas* para énfasis\n"
    "- ```bloques de código```\n"
    "- ![imagen](url) para recursos visuales\n"
    "Usa como maximo markdown nivel 3\n"
    "Prioriza diálogos socráticos y estructura tus respuestas en secciones claras.\n"
    "Utiliza el historial de conversaciones si está disponible para adaptar la explicación al alumno según sus características de aprendizaje.\n"
    "Cualquier ecuacion que escribas la quiero en formato LaTex.\n"
    "Es muy importante que cada vez que hagas una pregunta conceptual, interrumpas tu explicación hasta obtener una respuesta. No sigas con la explicación hasta no tener una respuesta."
    "Recuerda, tienes las capacidad de aceptar archivos de texto. Usalos para guiarte en las explicaciones."
    "Recuerda, puedes abrir archivos de texto y ver su informacion y utilizarla."
)

# Configuración para subida de archivos
ALLOWED_EXTENSIONS = {'txt', 'md', 'pdf', 'docx'}

def allowed_file(filename):
    return '.' in filename and filename.rsplit('.', 1)[1].lower() in ALLOWED_EXTENSIONS

##############################################
# Funciones Auxiliares                       #
##############################################

def sanitizar_markdown(texto):
    texto = re.sub(r'<script>.*?</script>', '', texto, flags=re.DOTALL)
    texto = re.sub(r'javascript:', '', texto, flags=re.IGNORECASE)
    return texto

def procesar_comando(comando, contenido):
    prompts = {
        '/explicar': (
            f"Como profesor experto, explica el tema: {contenido} usando:\n"
            "Todo el historial disponible de las conversaciones con el alumno\n"
            "Determina su nivel actual y su estilo de estudio para darle una clase que más se adapte a él\n"
            "Por ejemplo, si es bueno con matemáticas, dale muchas ecuaciones, y así con todo\n"
            "Determina si para un dado tema (cuando sea pertinente) vale la pena una explicación escalar o vectorial\n"
            "Usa formato Markdown con secciones claras (##) y resalta fórmulas importantes.\n"
            "Como máximo usa markdown nivel 3\n"
            "Utiliza todo el historial de conversación si está disponible para adaptar la explicación al alumno.\n"
            "Expándete lo más posible en cada tema para que se entienda bien.\n"
            "Cuando hagas una pregunta, interrumpe la explicación hasta tener una respuesta, esto es muy importante. No sigas con tu explicación hasta no tener una respuesta."
        ),
        '/example': (
            f"Como profesor, muestra 3 ejemplos prácticos y originales sobre: {contenido}. "
            "Incluye: 1) Contexto realista 2) Explicación detallada 3) Aplicación práctica. "
            "Usa formato Markdown con ecuaciones cuando sea necesario."
        ),
        '/exercise': (
            f"Crea un ejercicio práctico sobre: {contenido} con:\n"
            "1) Enunciado claro\n"
            "2) Datos relevantes\n"
            "3) Guía paso a paso\n"
            "4) Solución matemática usando LaTeX.\n"
            "Estructura en secciones con ##"
        ),
        '/summary': (
            "Genera un resumen estructurado con:\n"
            "1) Tema principal\n"
            "2) 3-5 puntos clave (###)\n"
            "3) Diagrama conceptual (en formato texto)\n"
            "4) Ejercicio de autoevaluación.\n"
            "Usa viñetas y ecuaciones cuando corresponda."
        ),
        '/help': "Lista detallada de comandos disponibles y su funcionamiento:"
    }
    return prompts.get(comando, None)

##############################################
# Rutas de Autenticación                     #
##############################################

@app.route('/register', methods=['GET', 'POST'])
def register():
    if request.method == 'POST':
        username = request.form.get('username', '').strip()
        password = request.form.get('password', '').strip()
        
        if len(password) < 8:
            flash("La contraseña debe tener al menos 8 caracteres", "danger")
            return redirect(url_for('register'))
        
        if User.query.filter_by(username=username).first():
            flash("El usuario ya existe", "danger")
            return redirect(url_for('register'))
            
        new_user = User(
            username=username,
            password=generate_password_hash(password),
            memory_mode=False
        )
        db.session.add(new_user)
        db.session.commit()
        
        # Crear mensaje de sistema para el nuevo usuario
        system_msg = Message(
            user_id=new_user.id,
            role='system',
            content=system_prompt_content
        )
        db.session.add(system_msg)
        db.session.commit()
        
        flash("Registro exitoso. Inicia sesión.", "success")
        return redirect(url_for('login'))
    return render_template('register.html')

@app.route('/login', methods=['GET', 'POST'])
def login():
    if request.method == 'POST':
        username = request.form.get('username', '').strip()
        password = request.form.get('password', '').strip()
        user = User.query.filter_by(username=username).first()
        
        if user and check_password_hash(user.password, password):
            login_user(user)
            flash("Inicio de sesión exitoso", "success")
            return redirect(url_for('index'))
        else:
            flash("Credenciales inválidas", "danger")
            return redirect(url_for('login'))
    return render_template('login.html')

@app.route('/logout')
@login_required
def logout():
    logout_user()
    flash("Sesión cerrada", "info")
    return redirect(url_for('login'))

##############################################
# Rutas del Chat                             #
##############################################

@app.route('/')
@login_required
def index():
    return render_template('index.html', memoriaActiva=current_user.memory_mode)

@app.route('/chat', methods=['POST'])
@login_required
def chat():
    user_message = request.json.get('message', '').strip()
    if not user_message:
        return jsonify({"response": "⚠️ Por favor escribe un mensaje válido"}), 400

    if user_message.startswith('/clear'):
        try:
            Message.query.filter(
                Message.user_id == current_user.id,
                Message.role != 'system'
            ).delete()
            db.session.commit()
            return jsonify({"response": "🔄 Historial borrado correctamente"})
        except Exception as e:
            app.logger.error(f"Error al borrar historial: {str(e)}")
            return jsonify({"response": "⚠️ Error al borrar el historial"}), 500

    user_msg = Message(
        user_id=current_user.id,
        role='user',
        content=user_message
    )
    db.session.add(user_msg)
    db.session.commit()

    try:
        mensajes_db = Message.query.filter_by(user_id=current_user.id).order_by(Message.timestamp.asc()).all()
        
        if current_user.memory_mode:
            mensajes = [{"role": msg.role, "content": msg.content} for msg in mensajes_db]
        else:
            system_msg = next((msg for msg in mensajes_db if msg.role == 'system'), None)
            last_msg = mensajes_db[-1] if mensajes_db else None
            mensajes = []
            if system_msg:
                mensajes.append({"role": system_msg.role, "content": system_msg.content})
            if last_msg:
                mensajes.append({"role": last_msg.role, "content": last_msg.content})

        if user_message.startswith('/'):
            comando = user_message.split()[0].lower()
            contenido = user_message[len(comando):].strip()
            prompt_comando = procesar_comando(comando, contenido)
            if prompt_comando:
                mensajes.append({"role": "user", "content": prompt_comando})

        response = openai.chat.completions.create(
            model="gpt-4o-mini",
            messages=mensajes,
            max_tokens=15000,
            temperature=0.7
        )
        bot_response = sanitizar_markdown(response.choices[0].message.content)
        
        assistant_msg = Message(
            user_id=current_user.id,
            role='assistant',
            content=bot_response
        )
        db.session.add(assistant_msg)
        db.session.commit()
        
        return jsonify({"response": bot_response})
    
    except Exception as e:
        db.session.rollback()
        app.logger.error(f"Error en OpenAI: {str(e)}")
        return jsonify({"response": "⚠️ Error al procesar tu solicitud. Intenta nuevamente."}), 500

@app.route('/export', methods=['GET'])
@login_required
def export_chat():
    try:
        messages = Message.query.filter(
            Message.user_id == current_user.id,
            Message.role != 'system'
        ).order_by(Message.timestamp.asc()).all()
        
        chat_content = "\n".join(
            f"{msg.role.capitalize()} ({msg.timestamp}): {msg.content}" 
            for msg in messages
        )
        
        response = make_response(chat_content)
        response.headers["Content-Disposition"] = f"attachment; filename=chat_export_{datetime.now().strftime('%Y%m%d%H%M')}.txt"
        response.headers["Content-type"] = "text/plain"
        return response
    except Exception as e:
        app.logger.error(f"Error en exportación: {str(e)}")
        return jsonify({"response": "⚠️ Error al exportar el chat"}), 500

@app.route('/toggle_memoria', methods=['POST'])
@login_required
def toggle_memoria():
    try:
        current_user.memory_mode = not current_user.memory_mode
        db.session.commit()
        return jsonify({
            "status": "success",
            "memory_mode": current_user.memory_mode,
            "message": f"Modo memoria {'activado' if current_user.memory_mode else 'desactivado'}"
        })
    except Exception as e:
        db.session.rollback()
        app.logger.error(f"Error al cambiar modo memoria: {str(e)}")
        return jsonify({"status": "error", "message": "Error al cambiar configuración"}), 500

@app.route('/resumen', methods=['GET'])
@login_required
def resumen():
    try:
        puntos_clave = Message.query.filter(
            Message.user_id == current_user.id,
            Message.role == 'assistant',
            Message.content.like('%### Punto clave%')
        ).order_by(Message.timestamp.asc()).all()
        
        if not puntos_clave:
            return jsonify({"response": "ℹ️ Aún no hay suficiente información para generar un resumen."})
        
        resumen_text = "## Resumen de aprendizaje\n" + "\n\n".join(
            f"### {msg.timestamp}\n{msg.content}" 
            for msg in puntos_clave
        )
        return jsonify({"response": resumen_text})
    except Exception as e:
        app.logger.error(f"Error en resumen: {str(e)}")
        return jsonify({"response": "⚠️ Error al generar el resumen"}), 500

##############################################
# Ruta para subir archivos (incluyendo PDF y DOCX)
##############################################

@app.route('/upload', methods=['POST'])
@login_required
def upload():
    if 'file' not in request.files:
        return jsonify({"error": "No se encontró el archivo en la petición."}), 400

    file = request.files['file']

    if file.filename == '':
        return jsonify({"error": "No se seleccionó ningún archivo."}), 400

    if file and allowed_file(file.filename):
        filename = secure_filename(file.filename)
        file_extension = filename.rsplit('.', 1)[1].lower()
        try:
            if file_extension == 'pdf':
                try:
                    pdf_reader = PyPDF2.PdfReader(file)
                    text = ""
                    for page in pdf_reader.pages:
                        page_text = page.extract_text()
                        if page_text:
                            text += page_text + "\n"
                    file_content = text if text.strip() != "" else "No se pudo extraer texto del PDF."
                except Exception as e:
                    app.logger.error(f"Error al procesar el PDF: {e}")
                    return jsonify({"error": "Error al procesar el archivo PDF."}), 500
            elif file_extension == 'docx':
                try:
                    doc = Document(file)
                    text = ""
                    for para in doc.paragraphs:
                        text += para.text + "\n"
                    file_content = text if text.strip() != "" else "No se pudo extraer texto del archivo DOCX."
                except Exception as e:
                    app.logger.error(f"Error al procesar el DOCX: {e}")
                    return jsonify({"error": "Error al procesar el archivo DOCX."}), 500
            else:
                file_content = file.read().decode('utf-8')
        except Exception as e:
            app.logger.error(f"Error leyendo el archivo: {e}")
            return jsonify({"error": "Error al leer el archivo."}), 500

        # Se guarda el contenido extraído en la base de datos como mensaje (para el contexto de GPT)
        file_message = Message(
            user_id=current_user.id,
            role='user',
            content=f"[Archivo Subido: {filename}]\n\n{file_content}"
        )
        db.session.add(file_message)
        db.session.commit()

        confirmation_msg = Message(
            user_id=current_user.id,
            role='system',
            content="Información del archivo procesada. Recuerda: has leído y comprendido el contenido del archivo subido."
        )
        db.session.add(confirmation_msg)
        db.session.commit()

        # Nota: Si no deseas mostrar el contenido del archivo en el chat, solo responde un mensaje de confirmación.
        return jsonify({"message": "Archivo subido correctamente.", "content": file_content})
    else:
        return jsonify({"error": "Tipo de archivo no permitido. Solo se permiten archivos de texto (.txt, .md), PDF (.pdf) y DOCX (.docx)."}), 400

if __name__ == '__main__':
    with app.app_context():
        db.create_all()
    app.run(debug=False)


