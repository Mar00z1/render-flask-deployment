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
COMANDOS_VALIDOS = ['/clear', '/example', '/exercise', '/summary', '/help','/explicar']

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
        'Utiliza el historial de conversaciones si esta disponible para poder adaptar la explicacion al alumno segun sus caracteristicas de aprendizaje.'
        'Cualquier ecuacion que escribas la quiero en formato LaTex'
        'Cuando hagas una pregunta, interrumpe la explicacion hasta tener una respuesta'
        'Los temas con conceptos matematicos, antes de explicarlos pregunta con que nivel matematico debes explicarlos y si de forma escalar o vectorial'
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
    """
    Genera prompts contextualizados para los comandos especiales
    que serán procesados por el modelo de OpenAI
    """
    prompts = {
        '/explicar': (
            f"Como profesor experto, explica el tema: {contenido} usando:\n"
            "Todo el historial disponible de las conversaciones con el alumno"
            'Determina su nivel actual y su estilo de estudio para darle una clase que mas se adapte a el'
            'Por ejemplo, si es bueno con matematicas, dale muchas ecuaciones, y asi con todo'
            "Usa formato Markdown con secciones claras (##) y resalta fórmulas importantes."
            'Utiliza todo el historial de conversacion si esta disponible para adaptar la explicacion al alumno.'
            'Me gustaria que te expandas lo mas posible en cada tema para que se entienda bien'
            'Los temas con conceptos matematicos, antes de explicarlos pregunta con que nivel matematico debes explicarlos y si de forma escalar o vectorial'
            
            
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

    # Manejar comandos especiales
    if user_message.startswith('/'):
        comando = user_message.split()[0].lower()
        contenido = user_message[len(comando):].strip()
        
        if comando not in COMANDOS_VALIDOS:
            return jsonify({"response": f"❌ Comando no reconocido: {comando}"}), 400
            
        # Obtener prompt contextualizado
        prompt_comando = procesar_comando(comando, contenido)
        if not prompt_comando:
            return jsonify({"response": "⚠️ Error en el comando"}), 400
        
        # Agregar el comando como mensaje de usuario
        conversation_history.append({"role": "user", "content": prompt_comando})
    else:
        # Mensaje normal
        conversation_history.append({"role": "user", "content": user_message})
    
    try:
        # Crear lista de mensajes según el modo memoria
        mensajes = conversation_history if modo_memoria_activado else [system_prompt, conversation_history[-1]]
        
        response = openai.chat.completions.create(
            model="gpt-4o-mini",
            messages=mensajes,
            max_tokens=5000,
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
