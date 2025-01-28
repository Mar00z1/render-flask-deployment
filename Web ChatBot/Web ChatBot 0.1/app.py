import openai
import json
from flask import Flask, render_template, request, jsonify
import os 
# Configuración de la clave API
openai.api_key = os.environ.get("OPENAI_API_KEY")

# Inicialización de Flask
app = Flask(__name__)

# Variable global para el historial de la conversación y el estado del modo memoria
conversation_history = [{"role": "system",
        "content":('Imagina que sos un excelente profesor, un explicador profesional. Das muchos ejemplos, explicas de una manera intuitiva cuando es necesario, pero de una manera lógica/matemática cuando se te pide, explicas con calma y paciencia. Cada vez que terminas de explicar un concepto, haces una muy buena pregunta para asegurar que el mismo se entendió, no sigues explicando hasta que esta pregunta te sea respondida. Además, luego de terminar toda tu explicación, das una guía de ejercicios para terminar de cerrar el tema. Es importante que hagas preguntas conceptuales interesantes luego de explicar cada tema. Espera mi respuesta a tu pregunta, no sigas explicando temas hasta que no tengas mi respuesta. Reacciona a mi respuesta de manera natural, dime claramente si he respondido mal una pregunta y refuerza tu explicación. Quiero que uses diagramas para tus explicaciones, no quiero que los generes, extráelos de internet. Además, si te proveo de una lista entera de temas, quiero que a partir de tu criterio resuelvas cuáles necesitarán una explicación más conceptual, y cuáles una explicación más matemática. Adicionalmente, si en estos temas aparecen ecuaciones vectoriales o cálculo vectorial en general, dejaré a tu criterio si es mejor explicar cierto tema de forma vectorial o escalar. Importante: si se te pide mostrar una imagen, quiero que sea extraida de internet, ademas, solo quiero que busques una sola imagen de lo solicitado, eso es muy importante. Estás listo?')}]
modo_memoria_activado = False  # Inicialmente, el modo memoria está desactivado

# Función para cargar el historial desde un archivo JSON
def cargar_historial():
    global conversation_history
    try:
        with open("modo_memoria.json", "r") as f:
            conversation_history = json.load(f)
    except FileNotFoundError:
        conversation_history = []

# Función para guardar el historial en un archivo JSON
def guardar_historial():
    with open("modo_memoria.json", "w") as f:
        json.dump(conversation_history, f)

# Ruta para la página principal
@app.route('/')
def index():
    return render_template('index.html')

# Ruta para recibir y responder mensajes del chatbot
@app.route('/chat', methods=['POST'])
def chat():
    global conversation_history, modo_memoria_activado

    user_message = request.json.get('message')
    if not user_message:
        return jsonify({"response": "Error: No se recibió ningún mensaje."}), 400

    # Agregar el mensaje del usuario al historial
    conversation_history.append({"role": "user", "content": user_message})

    # Si el modo memoria está activado, utilizamos el historial de conversaciones
    if modo_memoria_activado:
        chat_input = conversation_history
    else:
        chat_input = [{"role": "user", "content": user_message}]  # Solo el último mensaje del usuario

    try:
        response = openai.chat.completions.create(
            model="gpt-4o-mini",  # Usa el modelo GPT adecuado
            messages=chat_input,
            max_tokens=3000,
            temperature=0.7
        )

        # Respuesta del chatbot
        bot_response = response.choices[0].message.content

        # Agregar la respuesta del chatbot al historial
        conversation_history.append({"role": "assistant", "content": bot_response})

        # Guardar el historial en el archivo JSON
        guardar_historial()

        return jsonify({"response": bot_response})
    except Exception as e:
        return jsonify({"response": f"Ha ocurrido un error: {e}"}), 500

# Ruta para activar/desactivar el modo memoria
@app.route('/toggle_memoria', methods=['POST'])
def toggle_memoria():
    global modo_memoria_activado
    modo_memoria_activado = not modo_memoria_activado  # Cambiar el estado del modo memoria

    # Responder con el estado actual del modo memoria
    estado = "activado" if modo_memoria_activado else "desactivado"
    return jsonify({"response": f"Modo memoria {estado}."})

# Ruta para devolver lo aprendido por el chatbot (resumen)
@app.route('/resumen', methods=['POST'])
def resumen():
    global conversation_history

    # Crear un resumen del historial de conversación
    resumen_texto = "Lo que he aprendido sobre ti es lo siguiente:\n"
    for message in conversation_history:
        if message['role'] == 'user':
            resumen_texto += f"- {message['content']}\n"

    if not resumen_texto.strip() == "Lo que he aprendido sobre ti es lo siguiente:":
        return jsonify({"response": resumen_texto})
    else:
        return jsonify({"response": "No he aprendido mucho aún, ¡pero seguiré aprendiendo sobre ti!"})

if __name__ == '__main__':
    cargar_historial()  # Cargar el historial desde el archivo al iniciar
    app.run(debug=True)
