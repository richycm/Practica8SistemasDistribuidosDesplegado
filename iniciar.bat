@echo off
REM Levanta los 5 microservicios y el API Gateway sin Docker (cada uno en su ventana).
REM Requisito: pip install -r requirements.txt
cd /d "%~dp0"

start "Catalogo :5001"   cmd /k "cd catalog-service && python app.py"
start "Carrito :5002"    cmd /k "cd cart-service && python app.py"
start "Pagos :5003"      cmd /k "cd payment-service && python app.py"
start "Biblioteca :5004" cmd /k "cd library-service && python app.py"
start "Resenas :5005"    cmd /k "cd review-service && python app.py"
timeout /t 3 /nobreak >nul
start "API Gateway :5000" cmd /k "cd api-gateway && python app.py"
timeout /t 2 /nobreak >nul
start http://localhost:5000/
