import openai
import re
from flask import Flask, render_template, request, jsonify, make_response
from flask_sqlalchemy import SQLAlchemy
import os
from flask_migrate import Migrate
from datetime import datetime

# Configuración de la clave API
openai.api_key = os.environ.get("OPENAI_API_KEY")

# Inicialización de Flask y SQLAlchemy
app = Flask(__name__)
app.config['SQLALCHEMY_DATABASE_URI'] = 'sqlite:///conversations.db'
app.config['SQLALCHEMY_TRACK_MODIFICATIONS'] = False
db = SQLAlchemy(app)
migrate=Migrate(app,db)

# Modelo de base de datos para almacenar mensajes
class Message(db.Model):
    id = db.Column(db.Integer, primary_key=True)
    role = db.Column(db.String(50), nullable=False)
    content = db.Column(db.Text, nullable=False)
    timestamp = db.Column(db.DateTime, default=datetime.utcnow)

# Sistema de almacenamiento
conversation_history = []
modo_memoria_activado = False
COMANDOS_VALIDOS = ['/clear', '/example', '/exercise', '/summary', '/help', '/explicar']

# Prompt del sistema mejorado
system_prompt_content = (
    "Eres un profesor experto que adapta explicaciones usando ejemplos prácticos y preguntas interactivas. "
    "Usa formato Markdown para:\n"
    "- Encabezados (###)\n"
    "- **Negritas** para términos clave\n"
    "- *Cursivas* para énfasis\n"
    "- ```bloques de código```\n"
    "- ![imagen](url) para recursos visuales\n"
    'Usa como maximo markdown nivel 3'
    "Prioriza diálogos socráticos y estructura tus respuestas en secciones claras."
    'Utiliza el historial de conversaciones si esta disponible para poder adaptar la explicacion al alumno segun sus caracteristicas de aprendizaje.'
    'Cualquier ecuacion que escribas la quiero en formato LaTex'
    'Es muy importante que cada ve que hagas una pregunta conceptual, interrumpas tu explicacion hasta obtener una respuesta a la misma. No sigas con la explicacion hasta no tener una respuesta'
)

# Cargar historial al iniciar
def cargar_historial():
    global conversation_history
    conversation_history = []
    
    with app.app_context():
        db.create_all()
        
        system_msg = Message.query.filter_by(role='system').first()
        
        # Verificar si existe y coincide con la última versión
        if not system_msg or system_msg.content != system_prompt_content:
            # Eliminar versión vieja si existe
            if system_msg:
                db.session.delete(system_msg)
            
            # Crear nuevo mensaje de sistema
            new_system_msg = Message(
                role='system',
                content=system_prompt_content
            )
            db.session.add(new_system_msg)
            db.session.commit()
        
        # Cargar todos los mensajes ordenados por timestamp
        messages = Message.query.order_by(Message.timestamp.asc()).all()
        for msg in messages:
            conversation_history.append({
                "role": msg.role,
                "content": msg.content
            })

# Procesamiento seguro de Markdown
def sanitizar_markdown(texto):
    texto = re.sub(r'<script>.*?</script>', '', texto, flags=re.DOTALL)
    texto = re.sub(r'javascript:', '', texto, flags=re.IGNORECASE)
    return texto

# Manejo de comandos especiales
def procesar_comando(comando, contenido):
    prompts = {
        '/explicar': (
            f"Como profesor experto, explica el tema: {contenido} usando:\n"
            "Todo el historial disponible de las conversaciones con el alumno\n"
            "Determina su nivel actual y su estilo de estudio para darle una clase que mas se adapte a el\n"
            "Por ejemplo, si es bueno con matematicas, dale muchas ecuaciones, y asi con todo\n"
            'Por ejemplo, determina si para un dado tema (cuando sea pertinente), vale la pena una explicacion escalar o vectorial'
            "Usa formato Markdown con secciones claras (##) y resalta fórmulas importantes.\n"
            'Como maximo usa markdown nivel 3'
            "Utiliza todo el historial de conversacion si esta disponible para adaptar la explicacion al alumno.\n"
            "Me gustaria que te expandas lo mas posible en cada tema para que se entienda bien\n"
            "Cuando hagas una pregunta, interrumpe la explicacion hasta tener una respuesta, esto es muy importante. No sigas con tu explicacion hasta no tener una respuesta"
        ),
        '/example': (
            f"Como profesor, muestra 3 ejemplos prácticos y originales sobre: {contenido}. "
            "Incluye: 1) Contexto realista 2) Explicación detallada 3) Aplicación práctica. "
            "Usa formato Markdown con ecuaciones cuando sea necesario."
        ),
        '/exercise': (
            f"Crea un ejercicio práctico sobre: {contenido} con: "
            "1) Enunciado claro 2) Datos relevantes 3) Guía paso a paso "
            "4) Solución matemática usando LaTeX. Estructura en secciones con ##"
        ),
        '/summary': (
            "Genera un resumen estructurado con: "
            "1) Tema principal 2) 3-5 puntos clave (###) "
            "3) Diagrama conceptual (en formato texto) 4) Ejercicio de autoevaluación. "
            "Usa viñetas y ecuaciones cuando corresponda."
        ),
        '/help': "Lista detallada de comandos disponibles y su funcionamiento:"
    }
    return prompts.get(comando, None)

# Ruta principal
@app.route('/')
def index():
    return render_template('index.html')

# Ruta para el chat
@app.route('/chat', methods=['POST'])
def chat():
    global conversation_history, modo_memoria_activado

    user_message = request.json.get('message', '').strip()
    if not user_message:
        return jsonify({"response": "⚠️ Por favor escribe un mensaje válido"}), 400

    # Manejar comando /clear
    if user_message.startswith('/clear'):
        try:
            # Borrar todos los mensajes excepto el sistema
            Message.query.filter(Message.role != 'system').delete()
            db.session.commit()
            cargar_historial()
            return jsonify({"response": "🔄 Historial borrado correctamente"})
        except Exception as e:
            app.logger.error(f"Error al borrar historial: {str(e)}")
            return jsonify({"response": "⚠️ Error al borrar el historial"}), 500

    # Manejar otros comandos
    if user_message.startswith('/'):
        comando = user_message.split()[0].lower()
        contenido = user_message[len(comando):].strip()
        
        if comando not in COMANDOS_VALIDOS:
            return jsonify({"response": f"❌ Comando no reconocido: {comando}"}), 400
            
        prompt_comando = procesar_comando(comando, contenido)
        if not prompt_comando:
            return jsonify({"response": "⚠️ Error en el comando"}), 400
        
        # Guardar comando como mensaje de usuario
        user_msg = Message(role='user', content=prompt_comando)
        db.session.add(user_msg)
        conversation_history.append({"role": "user", "content": prompt_comando})
    else:
        # Mensaje normal
        user_msg = Message(role='user', content=user_message)
        db.session.add(user_msg)
        conversation_history.append({"role": "user", "content": user_message})
    
    try:
        db.session.commit()
        
        # Crear lista de mensajes según el modo memoria
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
        
        # Guardar respuesta del asistente
        assistant_msg = Message(role='assistant', content=bot_response)
        db.session.add(assistant_msg)
        db.session.commit()
        
        conversation_history.append({"role": "assistant", "content": bot_response})

        return jsonify({"response": bot_response})
    
    except Exception as e:
        db.session.rollback()
        app.logger.error(f"Error en OpenAI: {str(e)}")
        return jsonify({"response": "⚠️ Error al procesar tu solicitud. Intenta nuevamente."}), 500

# Ruta para exportar el chat
@app.route('/export', methods=['GET'])
def export_chat():
    try:
        messages = Message.query.filter(Message.role != 'system').order_by(Message.timestamp.asc()).all()
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

# Ruta para el modo memoria
@app.route('/toggle_memoria', methods=['POST'])
def toggle_memoria():
    global modo_memoria_activado
    modo_memoria_activado = not modo_memoria_activado
    return jsonify({
        "status": "success",
        "message": f"Modo memoria {'activado' if modo_memoria_activado else 'desactivado'}"
    })

# Ruta para obtener resumen
@app.route('/resumen', methods=['GET'])
def resumen():
    try:
        puntos_clave = Message.query.filter(
            Message.role == 'assistant',
            Message.content.like('%### Punto clave%')
        ).order_by(Message.timestamp.asc()).all()
        
        if not puntos_clave:
            return jsonify({"response": "ℹ️ Aún no hay suficiente información para generar un resumen."})
        
        resumen = "## Resumen de aprendizaje\n" + "\n\n".join(
            f"### {msg.timestamp}\n{msg.content}" 
            for msg in puntos_clave
        )
        return jsonify({"response": resumen})
    except Exception as e:
        app.logger.error(f"Error en resumen: {str(e)}")
        return jsonify({"response": "⚠️ Error al generar el resumen"}), 500

if __name__ == '__main__':
    with app.app_context():
        cargar_historial()
    app.run(debug=False)