import os
import sys
from PySide6.QtWidgets import QApplication,QMessageBox
from app.window import MainWindow

def main():
    if len(sys.argv)>2 and sys.argv[1]=='--self-test':
        from app.diagnostics import self_test
        return self_test(sys.argv[2])
    app=QApplication(sys.argv);app.setStyle('Fusion')
    app.setStyleSheet('QWidget {font-family: Segoe UI; font-size: 13px;} QGroupBox {font-weight:600;} QComboBox,QLineEdit,QSpinBox,QDoubleSpinBox {padding:5px;} QPushButton {padding:7px;}')
    try:
        window=MainWindow();window.show()
        if len(sys.argv)>1:window.load_file(sys.argv[1])
        return app.exec()
    except Exception as e:
        QMessageBox.critical(None,'Ошибка запуска',str(e));return 1

if __name__=='__main__':sys.exit(main())
