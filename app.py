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

# Configuración de la clave API de OpenAI
openai.api_key = os.environ.get("OPENAI_API_KEY")

# Inicialización de Flask y configuración
app = Flask(__name__)
app.config['SECRET_KEY'] = os.environ.get("SECRET_KEY", "este-es-un-secreto")
database_url = os.environ.get("DATABASE_URL", "sqlite:///conversations.db")
app.config['SQLALCHEMY_DATABASE_URI'] = database_url
app.config['SQLALCHEMY_TRACK_MODIFICATIONS'] = False

# Inicialización de la base de datos y migraciones
db = SQLAlchemy(app)
migrate = Migrate(app, db)

# Inicialización de Flask-Login
login_manager = LoginManager()
login_manager.init_app(app)
login_manager.login_view = 'login'  # Ruta a la que se redirige si no se está autenticado

##############################################
# Modelos: Usuario y Mensaje                 #
##############################################

# Modelo de Usuario (para autenticación)
class User(db.Model, UserMixin):
    id = db.Column(db.Integer, primary_key=True)
    username = db.Column(db.String(150), unique=True, nullable=False)
    password = db.Column(db.String(150), nullable=False)
    # Relación: un usuario puede tener muchos mensajes
    messages = db.relationship('Message', backref='user', lazy=True)

# Modelo de Mensaje (cada mensaje se asocia a un usuario)
class Message(db.Model):
    id = db.Column(db.Integer, primary_key=True)
    role = db.Column(db.String(50), nullable=False)
    content = db.Column(db.Text, nullable=False)
    timestamp = db.Column(db.DateTime, default=datetime.utcnow)
    user_id = db.Column(db.Integer, db.ForeignKey('user.id'), nullable=False)

##############################################
# Configuración de Flask-Login               #
##############################################

@login_manager.user_loader
def load_user(user_id):
    return User.query.get(int(user_id))

##############################################
# Variables Globales y Prompt del Sistema      #
##############################################

conversation_history = []
modo_memoria_activado = False
COMANDOS_VALIDOS = ['/clear', '/example', '/exercise', '/summary', '/help', '/explicar']

# Prompt del sistema para iniciar la conversación
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
)

##############################################
# Funciones Auxiliares                       #
##############################################

def cargar_historial():
    """
    Carga el historial de mensajes del usuario logueado. Si no existe o no coincide el mensaje de sistema,
    se crea o actualiza.
    """
    global conversation_history
    conversation_history = []
    
    # Asegurarse de que la base de datos esté creada
    with app.app_context():
        db.create_all()
        
        # Buscar el mensaje de sistema para el usuario actual
        system_msg = Message.query.filter_by(user_id=current_user.id, role='system').first()
        
        # Si no existe o el contenido no coincide con la última versión, se crea/actualiza
        if not system_msg or system_msg.content != system_prompt_content:
            if system_msg:
                db.session.delete(system_msg)
            new_system_msg = Message(
                user_id=current_user.id,
                role='system',
                content=system_prompt_content
            )
            db.session.add(new_system_msg)
            db.session.commit()
        
        # Cargar todos los mensajes del usuario actual ordenados por timestamp
        messages = Message.query.filter_by(user_id=current_user.id).order_by(Message.timestamp.asc()).all()
        for msg in messages:
            conversation_history.append({
                "role": msg.role,
                "content": msg.content
            })

def sanitizar_markdown(texto):
    """
    Limpia el contenido de posibles etiquetas o scripts maliciosos.
    """
    texto = re.sub(r'<script>.*?</script>', '', texto, flags=re.DOTALL)
    texto = re.sub(r'javascript:', '', texto, flags=re.IGNORECASE)
    return texto

def procesar_comando(comando, contenido):
    """
    Retorna el prompt correspondiente según el comando especial ingresado.
    """
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

# Ruta para registrar nuevos usuarios
@app.route('/register', methods=['GET', 'POST'])
def register():
    if request.method == 'POST':
        username = request.form.get('username', '').strip()
        password = request.form.get('password', '').strip()
        if not username or not password:
            flash("Por favor, ingresa usuario y contraseña", "danger")
            return redirect(url_for('register'))
        if User.query.filter_by(username=username).first():
            flash("El usuario ya existe", "danger")
            return redirect(url_for('register'))
        # En producción se recomienda hashear la contraseña
        new_user = User(username=username, password=password)
        db.session.add(new_user)
        db.session.commit()
        flash("Usuario registrado correctamente. Ahora inicia sesión.", "success")
        return redirect(url_for('login'))
    return render_template('register.html')

# Ruta para iniciar sesión
@app.route('/login', methods=['GET', 'POST'])
def login():
    if request.method == 'POST':
        username = request.form.get('username', '').strip()
        password = request.form.get('password', '').strip()
        user = User.query.filter_by(username=username).first()
        if user and user.password == password:
            login_user(user)
            flash("Has iniciado sesión correctamente", "success")
            return redirect(url_for('index'))
        else:
            flash("Usuario o contraseña incorrectos", "danger")
            return redirect(url_for('login'))
    return render_template('login.html')

# Ruta para cerrar sesión
@app.route('/logout')
@login_required
def logout():
    logout_user()
    flash("Has cerrado sesión", "info")
    return redirect(url_for('login'))

##############################################
# Rutas del Chat (Requieren Autenticación)    #
##############################################

# Ruta principal: interfaz del chatbot
@app.route('/')
@login_required
def index():
    cargar_historial()
    return render_template('index.html')

# Ruta para procesar mensajes del chat
@app.route('/chat', methods=['POST'])
@login_required
def chat():
    global conversation_history, modo_memoria_activado

    user_message = request.json.get('message', '').strip()
    if not user_message:
        return jsonify({"response": "⚠️ Por favor escribe un mensaje válido"}), 400

    # Manejar comando /clear: borrar mensajes del usuario actual (excepto el mensaje de sistema)
    if user_message.startswith('/clear'):
        try:
            Message.query.filter(
                Message.user_id == current_user.id,
                Message.role != 'system'
            ).delete()
            db.session.commit()
            cargar_historial()
            return jsonify({"response": "🔄 Historial borrado correctamente"})
        except Exception as e:
            app.logger.error(f"Error al borrar historial: {str(e)}")
            return jsonify({"response": "⚠️ Error al borrar el historial"}), 500

    # Manejar otros comandos especiales
    if user_message.startswith('/'):
        comando = user_message.split()[0].lower()
        contenido = user_message[len(comando):].strip()
        if comando not in COMANDOS_VALIDOS:
            return jsonify({"response": f"❌ Comando no reconocido: {comando}"}), 400
        prompt_comando = procesar_comando(comando, contenido)
        if not prompt_comando:
            return jsonify({"response": "⚠️ Error en el comando"}), 400
        # Guardar el comando como mensaje de usuario
        user_msg = Message(user_id=current_user.id, role='user', content=prompt_comando)
        db.session.add(user_msg)
        conversation_history.append({"role": "user", "content": prompt_comando})
    else:
        # Mensaje normal
        user_msg = Message(user_id=current_user.id, role='user', content=user_message)
        db.session.add(user_msg)
        conversation_history.append({"role": "user", "content": user_message})
    
    try:
        db.session.commit()
        
        # Crear la lista de mensajes según el modo memoria activado o no
        mensajes = conversation_history if modo_memoria_activado else [
            {"role": "system", "content": system_prompt_content},
            conversation_history[-1]
        ]
        
        response = openai.chat.completions.create(
            model="gpt-4o-mini",
            messages=mensajes,
            max_tokens=5000,
            temperature=0.7
        )
        bot_response = sanitizar_markdown(response.choices[0].message.content)
        
        # Guardar la respuesta del asistente
        assistant_msg = Message(user_id=current_user.id, role='assistant', content=bot_response)
        db.session.add(assistant_msg)
        db.session.commit()
        
        conversation_history.append({"role": "assistant", "content": bot_response})
        return jsonify({"response": bot_response})
    
    except Exception as e:
        db.session.rollback()
        app.logger.error(f"Error en OpenAI: {str(e)}")
        return jsonify({"response": "⚠️ Error al procesar tu solicitud. Intenta nuevamente."}), 500

# Ruta para exportar el chat en un archivo de texto
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

# Ruta para activar o desactivar el modo memoria
@app.route('/toggle_memoria', methods=['POST'])
@login_required
def toggle_memoria():
    global modo_memoria_activado
    modo_memoria_activado = not modo_memoria_activado
    return jsonify({
        "status": "success",
        "message": f"Modo memoria {'activado' if modo_memoria_activado else 'desactivado'}"
    })

# Ruta para obtener un resumen del chat
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
# Ejecución de la Aplicación                 #
##############################################

if __name__ == '__main__':
    # Si deseas cargar el historial al iniciar (para pruebas en desarrollo)
    with app.app_context():
        db.create_all()
    app.run(debug=False)
