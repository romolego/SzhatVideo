from PySide6.QtCore import Qt, QRectF, QSize, Signal
from PySide6.QtGui import QPainter, QPen, QColor, QPixmap
from PySide6.QtWidgets import (QDialog,QVBoxLayout,QHBoxLayout,QRadioButton,QCheckBox,
                              QLabel,QScrollArea,QWidget,QStackedWidget)
from .media import clock

class Wipe(QWidget):
    def __init__(self, original, compressed, parent=None):
        super().__init__(parent)
        self.original=QPixmap(original);self.compressed=QPixmap(compressed)
        self.split=.5;self.fit=True
        self.setMinimumSize(320,200)
        self.setMouseTracking(True)
        self.setCursor(Qt.SizeHorCursor)
    def sizeHint(self): return self.original.size() if not self.fit else QSize(960,540)
    def paintEvent(self,event):
        p=QPainter(self);p.fillRect(self.rect(),QColor('#10151d'))
        if self.original.isNull(): return
        size=self.original.size()
        if self.fit: size.scale(self.size(),Qt.KeepAspectRatio)
        rect=QRectF((self.width()-size.width())/2,(self.height()-size.height())/2,size.width(),size.height())
        p.setRenderHint(QPainter.SmoothPixmapTransform,True)
        p.drawPixmap(rect,self.compressed,QRectF(self.compressed.rect()))
        x=rect.left()+rect.width()*self.split
        p.save();p.setClipRect(QRectF(rect.left(),rect.top(),rect.width()*self.split,rect.height()))
        p.drawPixmap(rect,self.original,QRectF(self.original.rect()));p.restore()
        p.setPen(QPen(QColor('#44c8c0'),2));p.drawLine(int(x),int(rect.top()),int(x),int(rect.bottom()))
        p.setBrush(QColor('#44c8c0'));p.drawEllipse(int(x)-14,self.height()//2-14,28,28)
        p.setPen(QColor('#102c30'));p.drawText(int(x)-14,self.height()//2-14,28,28,Qt.AlignCenter,'↔')
        p.setPen(Qt.white)
        p.drawText(int(rect.left())+12,int(rect.top())+24,'Оригинал')
        p.drawText(int(rect.right())-85,int(rect.top())+24,'Сжато')
    def mousePressEvent(self,event): self.move_split(event)
    def mouseMoveEvent(self,event):
        if event.buttons() & Qt.LeftButton: self.move_split(event)
    def move_split(self,event):
        size=self.original.size()
        if self.fit:size.scale(self.size(),Qt.KeepAspectRatio)
        self.split=max(0,min(1,(event.position().x()-(self.width()-size.width())/2)/max(1,size.width())))
        self.update()

class FitImage(QLabel):
    def __init__(self,path):
        super().__init__();self.source=QPixmap(path);self.fit=True
        self.setAlignment(Qt.AlignCenter);self.setMinimumSize(100,100)
    def resizeEvent(self,event): self.refresh();super().resizeEvent(event)
    def refresh(self):
        self.setPixmap(self.source.scaled(self.size(),Qt.KeepAspectRatio,Qt.SmoothTransformation) if self.fit else self.source)
        self.setMinimumSize(100,100) if self.fit else self.setMinimumSize(self.source.size())

class Comparison(QDialog):
    preferences=Signal(str,bool)
    def __init__(self,item,mode,fit,parent=None):
        super().__init__(parent);self.setAttribute(Qt.WA_DeleteOnClose)
        self.setWindowTitle('Сравнение качества · '+clock(item['time']));self.resize(1200,760)
        root=QVBoxLayout(self);bar=QHBoxLayout()
        self.wipe_radio=QRadioButton('Шторка');self.side_radio=QRadioButton('Рядом')
        self.fit_check=QCheckBox('Вписать в окно');self.fit_check.setChecked(fit)
        bar.addWidget(self.wipe_radio);bar.addWidget(self.side_radio);bar.addSpacing(20);bar.addWidget(self.fit_check);bar.addStretch()
        bar.addWidget(QLabel('Потяните разделитель для сравнения'))
        root.addLayout(bar)
        self.stack=QStackedWidget();root.addWidget(self.stack,1)
        self.wipe=Wipe(item['original'],item['compressed'])
        self.wipe_scroll=QScrollArea();self.wipe_scroll.setWidget(self.wipe);self.stack.addWidget(self.wipe_scroll)
        side=QWidget();lay=QHBoxLayout(side);self.images=[];self.scrolls=[]
        for label,path in [('Оригинал',item['original']),('Сжато',item['compressed'])]:
            column=QVBoxLayout();column.addWidget(QLabel(label,alignment=Qt.AlignCenter))
            area=QScrollArea();image=FitImage(path);area.setWidget(image);column.addWidget(area,1)
            self.images.append(image);self.scrolls.append(area);lay.addLayout(column,1)
        self.stack.addWidget(side)
        root.addWidget(QLabel('Слева исходник, справа сжатый кадр. При уменьшении разрешения сжатый кадр растянут для сравнения.'))
        self.wipe_radio.setChecked(mode=='wipe');self.side_radio.setChecked(mode=='side')
        self.apply()
        self.wipe_radio.toggled.connect(self.changed);self.fit_check.toggled.connect(self.changed)
    def changed(self):
        self.apply();self.preferences.emit('wipe' if self.wipe_radio.isChecked() else 'side',self.fit_check.isChecked())
    def apply(self):
        fit=self.fit_check.isChecked();self.stack.setCurrentIndex(0 if self.wipe_radio.isChecked() else 1)
        self.wipe.fit=fit;self.wipe_scroll.setWidgetResizable(fit)
        if not fit:self.wipe.resize(self.wipe.original.size())
        else:self.wipe.setMinimumSize(320,200)
        self.wipe.update()
        for image,area in zip(self.images,self.scrolls):
            image.fit=fit;area.setWidgetResizable(fit)
            if not fit:image.resize(image.source.size())
            image.refresh()
