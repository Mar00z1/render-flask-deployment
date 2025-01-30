import openai
import json
import re
from flask import Flask, render_template, request, jsonify, make_response
import os
from datetime import datetime

# Configuración de la clave API
openai.api_key = os.environ.get("OPENAI_API_KEY")

# Inicialización de Flask
app = Flask(__name__)

# Sistema de almacenamiento
conversation_history = []
modo_memoria_activado = False
COMANDOS_VALIDOS = ['/clear', '/example', '/exercise', '/summary', '/help']

# Prompt del sistema mejorado
system_prompt = {
    "role": "system",
    "content": (
        "Eres un profesor experto que adapta explicaciones usando ejemplos prácticos y preguntas interactivas. "
        "Usa formato Markdown para:\n"
        "- Encabezados (###)\n"
        "- **Negritas** para términos clave\n"
        "- *Cursivas* para énfasis\n"
        "- ```bloques de código```\n"
        "- ![imagen](url) para recursos visuales\n"
        "Prioriza diálogos socráticos y estructura tus respuestas en secciones claras."
    )
}

# Cargar historial al iniciar
def cargar_historial():
    global conversation_history
    try:
        with open("modo_memoria.json", "r") as f:
            conversation_history = json.load(f)
    except FileNotFoundError:
        conversation_history = [system_prompt]

# Guardar historial
def guardar_historial():
    with open("modo_memoria.json", "w") as f:
        json.dump(conversation_history, f)

# Procesamiento seguro de Markdown
def sanitizar_markdown(texto):
    # Remover elementos potencialmente peligrosos
    texto = re.sub(r'<script>.*?</script>', '', texto, flags=re.DOTALL)
    texto = re.sub(r'javascript:', '', texto, flags=re.IGNORECASE)
    return texto

# Manejo de comandos especiales
def procesar_comando(comando, contenido):
    if comando == '/clear':
        conversation_history.clear()
        conversation_history.append(system_prompt)
        guardar_historial()
        return "Historial limpiado. ¿En qué tema quieres comenzar?"
    
    elif comando == '/example':
        return f"Por favor muestra 3 ejemplos prácticos sobre: {contenido}"
    
    elif comando == '/exercise':
        return f"Genera un ejercicio de práctica sobre: {contenido}"
    
    elif comando == '/summary':
        return "Genera un resumen estructurado con los puntos clave discutidos hasta ahora"
    
    elif comando == '/help':
        return "Comandos disponibles:\n" + "\n".join(COMANDOS_VALIDOS)
    
    return None

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

    # Manejar comandos
    if user_message.startswith('/'):
        comando = user_message.split()[0].lower()
        contenido = user_message[len(comando):].strip()
        
        if comando not in COMANDOS_VALIDOS:
            return jsonify({"response": f"❌ Comando no reconocido: {comando}"}), 400
            
        respuesta_comando = procesar_comando(comando, contenido)
        if respuesta_comando:
            return jsonify({"response": respuesta_comando})
    
    # Procesamiento normal del mensaje
    conversation_history.append({"role": "user", "content": user_message})
    
    try:
        response = openai.chat.completions.create(
            model="gpt-4o-mini",
            messages=conversation_history if modo_memoria_activado else [system_prompt, {"role": "user", "content": user_message}],
            max_tokens=1500,
            temperature=0.7
        )

        bot_response = sanitizar_markdown(response.choices[0].message.content)
        conversation_history.append({"role": "assistant", "content": bot_response})
        guardar_historial()

        return jsonify({"response": bot_response})
    
    except Exception as e:
        app.logger.error(f"Error en OpenAI: {str(e)}")
        return jsonify({"response": "⚠️ Error al procesar tu solicitud. Intenta nuevamente."}), 500

# Ruta para exportar el chat
@app.route('/export', methods=['GET'])
def export_chat():
    chat_content = "\n".join(
        f"{msg['role'].capitalize()}: {msg['content']}" 
        for msg in conversation_history 
        if msg['role'] != 'system'
    )
    
    response = make_response(chat_content)
    response.headers["Content-Disposition"] = f"attachment; filename=chat_export_{datetime.now().strftime('%Y%m%d%H%M')}.txt"
    response.headers["Content-type"] = "text/plain"
    return response

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
    puntos_clave = [
        msg['content'] for msg in conversation_history 
        if msg['role'] == 'assistant' and '### Punto clave' in msg['content']
    ]
    
    if not puntos_clave:
        return jsonify({"response": "ℹ️ Aún no hay suficiente información para generar un resumen."})
    
    resumen = "## Resumen de aprendizaje\n" + "\n\n".join(puntos_clave)
    return jsonify({"response": resumen})

if __name__ == '__main__':
    cargar_historial()
    app.run(debug=False)
