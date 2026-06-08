import sys
import os

# Adiciona o diretório raiz ao path para importar o módulo app
sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))

from app.main import app
