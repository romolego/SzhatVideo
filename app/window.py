from dataclasses import replace
from pathlib import Path
import tempfile
import shutil
import os
from PySide6.QtCore import Qt,QTimer,Signal,QPointF,QCoreApplication,QUrl,QProcess
from PySide6.QtGui import QPixmap,QPainter,QColor,QPen,QIcon,QWheelEvent,QDesktopServices
from PySide6.QtWidgets import (QMainWindow,QWidget,QVBoxLayout,QHBoxLayout,QGridLayout,
    QLabel,QLineEdit,QPushButton,QComboBox,QCheckBox,QSlider,QSpinBox,QDoubleSpinBox,
    QGroupBox,QTabWidget,QProgressBar,QFileDialog,QMessageBox,QStyleOptionSlider,QStyle,
    QScrollArea,QAbstractScrollArea,QSizePolicy,QLayout)
from . import settings
from .media import Job,auto_params,quality_for_crf,clock
from .workers import HardwareWorker,ProbeWorker,RecommendationWorker,PreviewWorker,EncodeWorker,SourceCache
from .compare import Comparison
from .profiles import profiles_for

def scroll_page(widget,event):
    parent=widget.parentWidget()
    while parent is not None:
        if isinstance(parent,QAbstractScrollArea):
            viewport=parent.viewport()
            forwarded=QWheelEvent(QPointF(viewport.mapFromGlobal(event.globalPosition().toPoint())),
                event.globalPosition(),event.pixelDelta(),event.angleDelta(),event.buttons(),event.modifiers(),
                event.phase(),event.inverted(),event.source(),event.pointingDevice())
            QCoreApplication.sendEvent(viewport,forwarded);event.accept();return
        parent=parent.parentWidget()
    event.ignore()

class ScrollSafeComboBox(QComboBox):
    def wheelEvent(self,event):
        if self.view().isVisible():super().wheelEvent(event);return
        scroll_page(self,event)

class ScrollSafeSpinBox(QSpinBox):
    def wheelEvent(self,event):scroll_page(self,event)

class ScrollSafeDoubleSpinBox(QDoubleSpinBox):
    def wheelEvent(self,event):scroll_page(self,event)

class QualitySlider(QSlider):
    recommendationClicked=Signal(str)
    def __init__(self):
        super().__init__(Qt.Horizontal);self.setRange(0,100);self.recommended=None;self.markers={}
        self.setFixedHeight(100);self.setSizePolicy(QSizePolicy.Expanding,QSizePolicy.Fixed);self.setMouseTracking(True)
        self.setToolTip('Влево — меньше размер и ниже качество. Вправо — выше качество и больше размер.')
    def wheelEvent(self,event):scroll_page(self,event)
    def marker_x(self,level=None):
        opt=QStyleOptionSlider();self.initStyleOption(opt)
        groove=self.style().subControlRect(QStyle.CC_Slider,opt,QStyle.SC_SliderGroove,self)
        handle=self.style().subControlRect(QStyle.CC_Slider,opt,QStyle.SC_SliderHandle,self)
        level=self.recommended if level is None else level
        return groove.left()+handle.width()/2+(groove.width()-handle.width())*level/100
    def marker_positions(self):
        return [(key,self.marker_x(self.markers[key]['level']),self.height()//2+14+row*14)
                for row,key in enumerate(('maximum','computer','phone')) if key in self.markers]
    def marker_at(self,pos):
        for key,x,y in self.marker_positions():
            left=x+11 if x+110<self.width() else x-105
            if abs(pos.y()-y)<=8 and (abs(pos.x()-x)<=14 or left<=pos.x()<=left+96):return key
        return None
    def mousePressEvent(self,event):
        key=self.marker_at(event.position())
        if key and event.button()==Qt.LeftButton:
            self.recommendationClicked.emit(key);event.accept();return
        super().mousePressEvent(event)
    def mouseMoveEvent(self,event):
        self.setCursor(Qt.PointingHandCursor if self.marker_at(event.position()) else Qt.ArrowCursor)
        super().mouseMoveEvent(event)
    def paintEvent(self,event):
        super().paintEvent(event)
        p=QPainter(self)
        for key,x,y in self.marker_positions():
            color={'maximum':'#18796e','computer':'#3767bd','phone':'#a86418'}[key]
            p.setPen(QPen(QColor(color),2));p.drawLine(int(x),self.height()//2+6,int(x),int(y)-8)
            profile_icon(key,color).paint(p,int(x)-7,int(y)-7,14,14)
            text={'maximum':'Максимум','computer':'Экран','phone':'Телефон'}[key]
            left=int(x)+11 if x+110<self.width() else int(x)-105
            p.drawText(left,int(y)-8,96,16,Qt.AlignVCenter|Qt.AlignLeft,text)

def profile_icon(kind,color='#167f75'):
    pix=QPixmap(24,24);pix.fill(Qt.transparent);p=QPainter(pix);p.setRenderHint(QPainter.Antialiasing)
    p.setPen(QPen(QColor(color),2))
    if kind=='phone':
        p.drawRoundedRect(6,2,12,20,2,2);p.drawLine(10,19,14,19)
    elif kind=='computer':
        p.drawRoundedRect(2,3,20,14,1,1);p.drawLine(12,17,12,21);p.drawLine(7,21,17,21)
    else:
        p.drawEllipse(5,2,14,14);p.drawLine(8,15,5,23);p.drawLine(16,15,19,23)
        p.drawLine(9,9,11,11);p.drawLine(11,11,16,6)
    p.end();return QIcon(pix)

class PreviewCard(QPushButton):
    def __init__(self,index):
        super().__init__();self.index=index;self.image=QPixmap();self.caption='Превью';self.setMinimumSize(220,170)
        self.setCursor(Qt.PointingHandCursor)
    def paintEvent(self,event):
        super().paintEvent(event)
        p=QPainter(self)
        if not self.image.isNull():
            pix=self.image.scaled(self.width()-20,self.height()-50,Qt.KeepAspectRatio,Qt.SmoothTransformation)
            p.drawPixmap((self.width()-pix.width())//2,10+(self.height()-50-pix.height())//2,pix)
        p.setPen(QColor('#596679'));p.drawText(0,self.height()-33,self.width(),22,Qt.AlignCenter,self.caption)
        p.setPen(QColor('#167f85'));p.drawText(0,self.height()-21,self.width(),22,Qt.AlignCenter,'Нажмите для сравнения')

class MainWindow(QMainWindow):
    def __init__(self):
        super().__init__();self.setWindowTitle('СжатьВидео 2.3');self.resize(1200,820);self.setMinimumSize(760,500)
        self.cfg=settings.load();self.media=None;self.gpu=[];self.hardware_ready=False;self.workers=[]
        self.current=None;self.encoding=None;self.generation=0;self.items=[];self.rec=None
        self.recommendations={};self.selected_profile=self.cfg['profile'];self.active_height=0
        self.recommend_worker=None;self.recommend_generation=0;self.target_revision=0;self.recommend_revision=0;self.recommend_apply=False
        self.resume_recommendation=False
        self.rec_signature=None;self.preview_pending=False
        self.last_output=self.cfg.get('last_output','')
        self.estimated=None;self.loading=True;self.closing=False;self.dialogs=[]
        self.temp=tempfile.TemporaryDirectory(prefix='sjvid2-')
        self.source_cache=SourceCache(Path(self.temp.name)/'sources')
        self.preview_timer=QTimer(self);self.preview_timer.setSingleShot(True);self.preview_timer.setInterval(180)
        self.preview_timer.timeout.connect(self.start_preview)
        self.recommend_timer=QTimer(self);self.recommend_timer.setSingleShot(True);self.recommend_timer.setInterval(450)
        self.recommend_timer.timeout.connect(lambda:self.start_recommendation(apply=False))
        self.save_timer=QTimer(self);self.save_timer.setSingleShot(True);self.save_timer.setInterval(350)
        self.save_timer.timeout.connect(self.persist)
        self.build();self.apply_cfg();self.loading=False;self.controls(False)
        available=self.screen().availableGeometry();self.resize(min(1200,available.width()-40),min(820,available.height()-60))
        self.hardware=HardwareWorker(self);self.hardware.done.connect(self.hardware_done)
        self.hardware.failed.connect(lambda e:self.hardware_done([]));self.retain(self.hardware)

    def retain(self,w):
        self.workers.append(w)
        w.finished.connect(lambda:self.release(w));w.start()
    def release(self,w):
        if w in self.workers:self.workers.remove(w)
        w.deleteLater()
        if self.closing and not self.workers:QTimer.singleShot(0,self.close)
    def combo(self,items):
        c=ScrollSafeComboBox()
        for text,data in items:c.addItem(text,data)
        return c
    def build(self):
        tabs=QTabWidget();self.setCentralWidget(tabs)
        main=QWidget();outer=QVBoxLayout(main);outer.setContentsMargins(0,0,0,0);tabs.addTab(main,'Сжатие')
        self.scroll=QScrollArea();self.scroll.setWidgetResizable(True);self.scroll.setFrameShape(QScrollArea.NoFrame)
        outer.addWidget(self.scroll,1)
        body=QWidget();root=QVBoxLayout(body);root.setSpacing(10);root.setSizeConstraint(QLayout.SetMinimumSize)
        self.scroll.setWidget(body)
        title=QLabel('Сжать видео');title.setStyleSheet('font-size:24px;font-weight:600;');root.addWidget(title)
        filebar=QHBoxLayout();self.file_edit=QLineEdit();self.file_edit.setPlaceholderText('Выберите видео или перетащите файл сюда')
        self.file_edit.setReadOnly(True);self.pick=QPushButton('Выбрать видео…');self.pick.clicked.connect(self.pick_file)
        filebar.addWidget(self.file_edit,1);filebar.addWidget(self.pick);root.addLayout(filebar)
        self.info_label=QLabel('Настройки сохраняются автоматически.');self.info_label.setWordWrap(True);root.addWidget(self.info_label)
        group=QGroupBox('Настройки');grid=QGridLayout(group);root.addWidget(group)
        self.codec=self.combo([('H.264','H.264'),('H.265 / HEVC','H.265 / HEVC')])
        self.engine=self.combo([('Авто (GPU / CPU)','auto'),('NVIDIA GPU (NVENC)','gpu'),('Процессор (CPU)','cpu')])
        self.container=self.combo([('MP4 (.mp4)','mp4'),('MKV (.mkv)','mkv')])
        self.preset=self.combo([('Максимальная скорость','fast'),('Средний','medium'),('Максимальное сжатие (медленнее)','slow')])
        self.audio=self.combo([('AAC','AAC'),('Копировать исходный звук','Копировать'),('Без звука','Без звука')])
        self.audio_kbps=self.combo([(str(n),n) for n in (96,128,160,192,256,320)])
        for row,label,a,label2,b in [(0,'Видео-кодек',self.codec,'Обработка',self.engine),
                (1,'Контейнер',self.container,'Скорость / эффективность',self.preset),
                (2,'Аудио',self.audio,'Битрейт аудио, кбит/с',self.audio_kbps)]:
            grid.addWidget(QLabel(label),row,0);grid.addWidget(a,row,1);grid.addWidget(QLabel(label2),row,2);grid.addWidget(b,row,3)
        grid.setColumnStretch(1,1);grid.setColumnStretch(3,1)
        self.hardware_label=QLabel('Проверка GPU…');grid.addWidget(self.hardware_label,3,0,1,4)
        target=QGroupBox('Качество и размер');target.setSizePolicy(QSizePolicy.Preferred,QSizePolicy.Minimum)
        lay=QVBoxLayout(target);lay.setSizeConstraint(QLayout.SetMinimumSize);root.addWidget(target)
        mode=QHBoxLayout();self.method=self.combo([('Авто: качество + разрешение','auto'),('Качество, разрешение вручную','quality'),('Целевой размер','size'),('Целевой битрейт','bitrate')])
        self.resolution=self.combo([('Как в исходнике',0)]+[(f'До {n}p',n) for n in (2160,1440,1080,720,480,360)])
        mode.addWidget(QLabel('Метод'));mode.addWidget(self.method,1);mode.addSpacing(20);mode.addWidget(QLabel('Разрешение'));mode.addWidget(self.resolution);lay.addLayout(mode)
        self.quality_panel=QWidget();qroot=QVBoxLayout(self.quality_panel);qroot.setContentsMargins(0,0,0,0)
        ends=QHBoxLayout();ends.addWidget(QLabel('Меньше файл · ниже качество'));ends.addStretch();ends.addWidget(QLabel('Выше качество · больше файл'));qroot.addLayout(ends)
        self.quality=QualitySlider();qroot.addWidget(self.quality);lay.addWidget(self.quality_panel)
        self.rate_panel=QWidget();rates=QHBoxLayout(self.rate_panel);rates.setContentsMargins(0,0,0,0)
        self.size_spin=ScrollSafeDoubleSpinBox();self.size_spin.setRange(1,1000000);self.size_spin.setSuffix(' МБ');self.size_spin.setDecimals(0)
        self.bitrate_spin=ScrollSafeSpinBox();self.bitrate_spin.setRange(32,200000);self.bitrate_spin.setSuffix(' кбит/с')
        self.size_label=QLabel('Размер');self.bitrate_label=QLabel('Битрейт видео')
        rates.addWidget(self.size_label);rates.addWidget(self.size_spin);rates.addWidget(self.bitrate_label);rates.addWidget(self.bitrate_spin);rates.addStretch();lay.addWidget(self.rate_panel)
        self.quality_label=QLabel();self.quality_label.setWordWrap(True);lay.addWidget(self.quality_label)
        recrow=QHBoxLayout();self.rec_button=QPushButton('Пересчитать рекомендации');self.rec_button.clicked.connect(self.recommend)
        recrow.addWidget(self.rec_button);self.rec_progress=QLabel();self.rec_progress.setWordWrap(True);recrow.addWidget(self.rec_progress,1);lay.addLayout(recrow)
        profiles=QHBoxLayout();self.profile_buttons={}
        for key,text in [('maximum','Максимум качества'),('computer','Ваш экран'),('phone','Телефон · 720p')]:
            button=QPushButton(text);button.setIcon(profile_icon(key));button.setCheckable(True);button.setEnabled(False)
            button.clicked.connect(lambda checked=False,k=key:self.apply_recommendation(k));profiles.addWidget(button,1);self.profile_buttons[key]=button
        lay.addLayout(profiles)
        self.rec_label=QLabel('Три рекомендации появятся после анализа коротких фрагментов.');self.rec_label.setWordWrap(True);lay.addWidget(self.rec_label)
        self.quality.recommendationClicked.connect(self.apply_recommendation)
        preview=QGroupBox('Превью готового результата');pvroot=QVBoxLayout(preview);root.addWidget(preview,1)
        cards=QHBoxLayout();self.cards=[]
        for n in range(3):
            card=PreviewCard(n);card.clicked.connect(lambda checked=False,i=n:self.open_compare(i));cards.addWidget(card,1);self.cards.append(card)
        pvroot.addLayout(cards,1);self.status=QLabel('Выберите видео.');self.status.setWordWrap(True);pvroot.addWidget(self.status)
        destination=QHBoxLayout();destination.addWidget(QLabel('Папка результата'))
        self.output_edit=QLineEdit();self.output_edit.setPlaceholderText('Рядом с исходным видео');destination.addWidget(self.output_edit,1)
        out=QPushButton('Обзор…');out.clicked.connect(self.pick_output);destination.addWidget(out);root.addLayout(destination)
        footer=QWidget();foot=QVBoxLayout(footer);foot.setContentsMargins(12,4,12,12);foot.setSpacing(5);outer.addWidget(footer)
        self.progress=QProgressBar();self.progress.setRange(0,1000);self.progress.setValue(0);foot.addWidget(self.progress)
        self.progress_label=QLabel('');self.progress_label.setWordWrap(True);foot.addWidget(self.progress_label)
        self.go=QPushButton('СЖАТЬ ВИДЕО');self.go.setMinimumHeight(52);self.go.setStyleSheet('background:#188b80;color:white;font-size:18px;font-weight:600;border-radius:6px;')
        self.go.clicked.connect(self.start_encoding);foot.addWidget(self.go)
        actions=QHBoxLayout()
        self.open_folder_button=QPushButton('Открыть папку с файлом');self.open_video_button=QPushButton('Открыть сжатое видео')
        self.open_folder_button.clicked.connect(self.open_result_folder);self.open_video_button.clicked.connect(self.open_result_video)
        actions.addWidget(self.open_folder_button);actions.addWidget(self.open_video_button);foot.addLayout(actions)
        self.update_result_actions()
        defaults=QWidget();d=QVBoxLayout(defaults);d.setSizeConstraint(QLayout.SetMinimumSize)
        default_scroll=QScrollArea();default_scroll.setWidgetResizable(True);default_scroll.setWidget(defaults);tabs.addTab(default_scroll,'По умолчанию')
        heading=QLabel('Настройки запоминаются автоматически');heading.setStyleSheet('font-size:22px;font-weight:600');d.addWidget(heading)
        text=QLabel('Метод, кодек, GPU/CPU, скорость, звук и папка результата сохраняются после изменения.\nРежим сравнения и масштаб также запоминаются между открытиями окна и перезапусками.');text.setWordWrap(True);d.addWidget(text)
        self.recommend_check=QCheckBox('При загрузке видео применять рекомендацию выбранного профиля (по умолчанию — максимум качества)');d.addWidget(self.recommend_check)
        self.compare_combo=self.combo([('Шторка','wipe'),('Рядом','side')]);r=QHBoxLayout();r.addWidget(QLabel('Сравнение по умолчанию'));r.addWidget(self.compare_combo);r.addStretch();d.addLayout(r)
        self.fit_check=QCheckBox('Вписывать сравнение в окно (отключите для масштаба 100%)');d.addWidget(self.fit_check)
        threshold=QHBoxLayout();threshold.addWidget(QLabel('Минимальный средний VMAF для рекомендации'))
        self.vmaf_spin=ScrollSafeDoubleSpinBox();self.vmaf_spin.setRange(90,99.9);self.vmaf_spin.setDecimals(1);self.vmaf_spin.setSingleStep(.5);threshold.addWidget(self.vmaf_spin);threshold.addStretch();d.addLayout(threshold)
        explanation=QLabel('Три фрагмента по 3 секунды, CQ/CRF от 12 до 28 с шагом 2. Максимум качества: исходное разрешение и первый подходящий вариант с уменьшением размера ≥ 3%. Ваш экран: разрешение ограничено физическими пикселями текущего экрана; выбирается наименьший размер среди подходящих вариантов. Телефон: ориентир 1280×720 (720×1280 для вертикального видео), порог VMAF на 2 балла ниже. Исходник никогда не увеличивается.\n\nVMAF измеряется в размере просмотра; средний балл должен быть не ниже порога, каждый фрагмент — не ниже порога минус 1. Это оценка визуального сходства, а не процент сохранённого качества. Размер и качество всего фильма могут отличаться от оценки. Рекомендация не появляется, если проверенные варианты не подходят.\n\nЗелёная отметка и кнопки рекомендаций применяют качество вместе с разрешением. При движении ползунка сохраняется ограничение выбранного профиля. Сохраняется первая видеодорожка и первая аудиодорожка; обрабатывается SDR.');explanation.setWordWrap(True);d.addWidget(explanation)
        self.defaults_status=QLabel();d.addWidget(self.defaults_status);d.addStretch()
        reset=QPushButton('Вернуть исходные настройки');reset.clicked.connect(self.reset_defaults);d.addWidget(reset)
        self.setAcceptDrops(True)
        self.inputs=[self.codec,self.engine,self.container,self.preset,self.audio,self.audio_kbps,self.method,self.resolution,self.quality,self.size_spin,self.bitrate_spin,self.output_edit,self.rec_button]
        for widget in [self.codec,self.engine,self.container,self.preset,self.audio,self.audio_kbps,self.method,self.resolution,self.compare_combo]:widget.currentIndexChanged.connect(self.changed)
        for widget in [self.quality,self.size_spin,self.bitrate_spin]:widget.valueChanged.connect(self.changed)
        self.quality.sliderPressed.connect(self.preview_timer.stop)
        self.quality.sliderReleased.connect(self.queue_preview)
        self.vmaf_spin.valueChanged.connect(self.changed)
        for widget in [self.recommend_check,self.fit_check]:widget.toggled.connect(self.changed)
        self.output_edit.textChanged.connect(self.changed)

    def update_result_actions(self):
        exists=bool(self.last_output) and Path(self.last_output).is_file()
        for button in (self.open_folder_button,self.open_video_button):
            button.setEnabled(exists);button.setToolTip(self.last_output if exists else 'Станет доступно после успешного сжатия.')
    def result_path(self):
        if not self.last_output or not Path(self.last_output).is_file():
            self.update_result_actions();self.status.setText('Последний результат перемещён или удалён.');return None
        return Path(self.last_output)
    def open_result_folder(self):
        path=self.result_path()
        if path is None:return
        ok,_=QProcess.startDetached('explorer.exe',['/select,',str(path)])
        if not ok:self.status.setText('Не удалось открыть папку: '+str(path.parent))
    def open_result_video(self):
        path=self.result_path()
        if path is not None and not QDesktopServices.openUrl(QUrl.fromLocalFile(str(path))):
            self.status.setText('Не удалось открыть видео: '+str(path))

    def set_combo(self,combo,data):
        n=combo.findData(data);combo.setCurrentIndex(n if n>=0 else 0)
    def apply_cfg(self):
        c=self.cfg
        self.selected_profile=c['profile'] if c['profile'] in ('maximum','computer','phone') else 'maximum'
        for combo,key in [(self.codec,'codec'),(self.engine,'engine'),(self.container,'container'),(self.preset,'preset'),(self.audio,'audio'),(self.audio_kbps,'audio_kbps'),(self.method,'method'),(self.resolution,'resolution'),(self.compare_combo,'compare')]:self.set_combo(combo,c[key])
        self.quality.setValue(c['quality']);self.size_spin.setValue(c['target_mb']);self.bitrate_spin.setValue(c['bitrate'])
        self.output_edit.setText(c['output_dir']);self.recommend_check.setChecked(c['recommend']);self.fit_check.setChecked(c['fit']);self.update_labels()
        self.vmaf_spin.setValue(c['min_vmaf'])
    def collect(self):
        cfg=self.cfg.copy()
        for combo,key in [(self.codec,'codec'),(self.engine,'engine'),(self.container,'container'),(self.preset,'preset'),(self.audio,'audio'),(self.audio_kbps,'audio_kbps'),(self.method,'method'),(self.resolution,'resolution'),(self.compare_combo,'compare')]:cfg[key]=combo.currentData()
        cfg.update(quality=self.quality.value(),target_mb=float(self.size_spin.value()),bitrate=self.bitrate_spin.value(),output_dir=self.output_edit.text(),recommend=self.recommend_check.isChecked(),fit=self.fit_check.isChecked(),min_vmaf=float(self.vmaf_spin.value()),profile=self.selected_profile,last_output=self.last_output,schema=3)
        return cfg
    def persist(self):
        self.cfg=self.collect()
        try:settings.save(self.cfg);self.defaults_status.setText('Настройки сохранены')
        except OSError as e:self.defaults_status.setText('Не удалось сохранить настройки: '+str(e))
    def reset_defaults(self):
        self.loading=True;self.cfg=settings.DEFAULTS.copy();self.apply_cfg();self.loading=False;self.persist();self.changed()
    def analysis_signature(self):
        return tuple(c.currentData() for c in (self.codec,self.engine,self.preset,self.audio,self.audio_kbps))+(self.vmaf_spin.value(),)
    def recommendations_current(self):
        return self.rec_signature==self.analysis_signature()
    def changed(self,*args):
        if self.loading:return
        self.save_timer.start();self.update_labels()
        sender=self.sender()
        if sender in [self.quality,self.method,self.resolution,self.size_spin,self.bitrate_spin]:self.target_revision+=1
        if self.rec_signature is not None and not self.recommendations_current():
            if self.recommend_worker:self.cancel_recommendation()
            self.recommend_apply=False
            self.rec_progress.setText('Настройки кодирования изменены. Для актуальных оценок нажмите «Пересчитать рекомендации».')
            for b in self.profile_buttons.values():b.setEnabled(False)
        elif self.rec_signature is not None:
            for key,b in self.profile_buttons.items():b.setEnabled(bool(self.recommendations.get(key,{}).get('found')) and not self.encoding)
        preview_inputs=[self.codec,self.engine,self.preset,self.audio,self.audio_kbps,self.method,self.resolution,self.quality,self.size_spin,self.bitrate_spin]
        if self.media and not self.encoding and (sender in preview_inputs or sender is None):
            self.queue_preview()
    def queue_preview(self):
        if not self.media or self.encoding:return
        self.generation+=1
        if self.current:self.current.cancel();self.current=None
        self.invalidate_preview(keep=True)
        self.preview_timer.stop()
        if not self.quality.isSliderDown():self.preview_timer.start()
    def invalidate_preview(self,keep=False):
        self.preview_pending=True;self.estimated=None
        if not keep:
            self.items=[]
            for c in self.cards:c.image=QPixmap();c.caption='Готовится…';c.update()
        elif self.items:
            for c in self.cards:c.caption='Обновляется…';c.update()
        if self.media:self.status.setText('Превью обновляется; показаны предыдущие кадры.' if keep and self.items else 'Готовится превью…')
        self.update_labels()
    def update_labels(self):
        method=self.method.currentData();quality_mode=method in ('auto','quality')
        self.quality_panel.setVisible(quality_mode);self.rate_panel.setVisible(not quality_mode)
        for w in (self.size_label,self.size_spin):w.setVisible(method=='size')
        for w in (self.bitrate_label,self.bitrate_spin):w.setVisible(method=='bitrate')
        self.resolution.setEnabled(method!='auto' and not self.encoding)
        self.audio_kbps.setEnabled(self.audio.currentData()=='AAC' and not self.encoding)
        if not self.media:self.quality_label.setText('Вправо — выше качество.');return
        job=self.job();q,h=job.params
        height=h or self.media.height
        base=f'{self.quality.value()}/100 на шкале · '+('CQ' if job.engine=='gpu' else 'CRF')+f' {q} · {height}p'
        if not quality_mode:base=f'Битрейт видео {job.video_rate} кбит/с · {height}p'
        if self.estimated:
            base+=f' · ≈ {self.estimated:.0f} МБ ({self.estimated/self.media.mb*100:.0f}% исходника, оценка по фрагментам)'
        self.quality_label.setText(base)
    def job(self):
        engine=self.engine.currentData();prefix='hevc' if self.codec.currentData()=='H.265 / HEVC' else 'h264'
        if engine=='auto':engine='gpu' if prefix in self.gpu else 'cpu'
        cap=self.active_height or next((p['height'] for p in self.profile_specs() if p['key']==self.selected_profile),0)
        job=Job(self.media,self.codec.currentData(),engine,self.preset.currentData(),self.method.currentData(),self.quality.value(),self.resolution.currentData(),self.audio.currentData(),self.audio_kbps.currentData(),self.size_spin.value(),self.bitrate_spin.value(),self.vmaf_spin.value(),cap)
        h=job.params[1] or self.media.height
        if self.engine.currentData()=='auto' and (h<160 or self.media.width*h/self.media.height<160):job=replace(job,engine='cpu')
        return job
    def hardware_done(self,data):
        self.hardware_ready=True
        self.gpu=data if isinstance(data,list) else []
        self.hardware_label.setText('NVIDIA NVENC проверен: '+', '.join(self.gpu)+'. Кодирование на GPU; часть операций использует CPU.' if self.gpu else 'NVIDIA NVENC недоступен. Режим «Авто» использует CPU.')
        if self.media and not self.encoding and not self.recommend_worker:self.after_load()
    def controls(self,enabled):
        for w in self.inputs:w.setEnabled(enabled)
        self.go.setEnabled(enabled);self.update_labels()
        # Настройки можно выбрать ещё до загрузки файла.
        if not self.encoding:
            for w in self.inputs:
                if w is not self.rec_button:w.setEnabled(True)
            self.update_labels()
        for key,b in self.profile_buttons.items():b.setEnabled(bool(self.recommendations.get(key,{}).get('found')) and not self.encoding and self.recommendations_current())
    def pick_file(self):
        path,_=QFileDialog.getOpenFileName(self,'Открыть видео',self.cfg['last_dir'],'Видео (*.mp4 *.mkv *.mov *.avi *.webm *.m4v *.ts *.mts);;Все файлы (*)')
        if path:self.load_file(path)
    def load_file(self,path):
        if self.encoding:return
        self.cancel_recommendation();self.target_revision+=1
        self.generation+=1;generation=self.generation;self.preview_timer.stop()
        if self.current:self.current.cancel();self.current=None
        self.media=None;self.rec_signature=None;self.clear_recommendations();self.invalidate_preview();self.controls(False)
        self.file_edit.setText(path);self.status.setText('Читаю информацию о видео…')
        w=ProbeWorker(path,self);self.current=w
        w.done.connect(lambda m:self.loaded(m,generation));w.failed.connect(lambda e:self.error(e,generation));self.retain(w)
    def loaded(self,m,generation):
        if generation!=self.generation or not hasattr(m,'duration'):return
        self.current=None
        if m.hdr:self.error('Это HDR-видео. Эта версия рассчитана на SDR и не будет менять HDR без отдельного преобразования.',generation);return
        self.media=m;self.cfg['last_dir']=str(Path(m.path).parent);self.save_timer.start();self.controls(True)
        self.info_label.setText(f'{m.width}×{m.height} · {clock(m.duration)} · {m.video_codec} · {m.audio_codec or "без звука"} · {m.mb:.0f} МБ')
        self.progress.setValue(0);self.progress_label.clear();self.rec_label.setText('Рекомендация ещё не рассчитана.')
        self.update_labels()
        if self.hardware_ready:self.after_load()
        else:self.status.setText('Ожидаю проверку GPU…')
    def after_load(self):
        self.start_recommendation()
        if not self.recommend_apply:self.start_preview()
    def pick_output(self):
        folder=QFileDialog.getExistingDirectory(self,'Папка результата',self.output_edit.text())
        if folder:self.output_edit.setText(folder)
    def recommend(self):
        if not self.media or self.encoding:return
        self.start_recommendation(apply=False)
    def profile_specs(self):
        screen=self.screen();size=screen.size();ratio=screen.devicePixelRatio()
        return profiles_for(self.media,(round(size.width()*ratio),round(size.height()*ratio)),self.vmaf_spin.value())
    def clear_recommendations(self):
        self.rec=None;self.recommendations={};self.active_height=0;self.quality.recommended=None;self.quality.update()
        self.quality.markers={};self.rec_progress.clear()
        for button in self.profile_buttons.values():button.setEnabled(False);button.setChecked(False)
    def apply_recommendation(self,key):
        if self.encoding:return
        if not self.recommendations_current():
            self.rec_label.setText('Эти метки рассчитаны для прежних настроек кодирования. Нажмите «Пересчитать рекомендации».');return
        r=self.recommendations.get(key)
        if not r or not r.get('found'):
            if self.recommendations:self.rec_label.setText((r or {}).get('reason','Нет подходящей рекомендации.'))
            return
        self.selected_profile=key;self.active_height=r['height'];self.rec=r;level=quality_for_crf(r['crf'])
        self.target_revision+=1;self.recommend_apply=False
        self.loading=True;self.set_combo(self.method,'auto');self.quality.setValue(level);self.loading=False
        self.quality.recommended=level;self.quality.update()
        for k,b in self.profile_buttons.items():b.setChecked(k==key)
        self.rec_label.setText(f'Рекомендация «{r["name"]}»: {r["height"]}p · ≈ {r["mb"]:.0f} МБ вместо {self.media.mb:.0f} МБ · VMAF {r["vmaf"]:.1f}/100 (3 фрагмента).')
        self.save_timer.start();self.queue_preview()
    def cancel_recommendation(self):
        self.recommend_timer.stop();self.recommend_generation+=1
        if self.recommend_worker:self.recommend_worker.cancel();self.recommend_worker=None
    def start_recommendation(self,apply=None):
        if not self.media or self.encoding or not self.hardware_ready:return
        self.cancel_recommendation();g=self.recommend_generation
        prefix='hevc' if self.codec.currentData()=='H.265 / HEVC' else 'h264'
        if self.engine.currentData()=='gpu' and ('hevc' if self.codec.currentData()=='H.265 / HEVC' else 'h264') not in self.gpu:
            self.rec_progress.setText('NVIDIA encoder недоступен. Выберите «Авто» или CPU.');return
        self.clear_recommendations();self.rec_button.setEnabled(True)
        self.rec_signature=self.analysis_signature()
        self.recommend_apply=(self.recommend_check.isChecked() and self.method.currentData()=='auto') if apply is None else apply
        self.recommend_revision=self.target_revision
        self.rec_progress.setText('Расчёт рекомендаций: подготовка фрагментов…');self.rec_label.setText('Готовые метки появятся на шкале по мере расчёта.')
        job=replace(self.job(),method='auto',quality=85,auto_height=0)
        if self.engine.currentData()=='auto':job=replace(job,engine='gpu' if prefix in self.gpu else 'cpu')
        w=RecommendationWorker(job,self,profiles=self.profile_specs(),source_cache=self.source_cache);self.recommend_worker=w
        w.status.connect(lambda s:self.recommendation_status(s,g))
        w.profileReady.connect(lambda key,r:self.profile_ready(key,r,g))
        w.done.connect(lambda r:self.recommended(r,g));w.failed.connect(lambda e:self.recommendation_error(e,g))
        w.finished.connect(lambda:self.recommendation_finished(w,g));self.retain(w)
    def recommendation_status(self,s,g):
        if g==self.recommend_generation:self.rec_progress.setText(s)
    def recommendation_finished(self,w,g):
        if g==self.recommend_generation and self.recommend_worker is w:
            self.recommend_worker=None
            self.rec_progress.setText('Расчёт завершён. Можно пересчитать рекомендации.')
        if not self.encoding:self.rec_button.setEnabled(bool(self.media))
    def recommendation_error(self,e,g):
        if g!=self.recommend_generation:return
        self.recommend_worker=None;self.rec_button.setEnabled(True);self.rec_progress.setText('Ошибка анализа: '+e)
        if not self.items and not self.current:self.start_preview()
    def profile_ready(self,key,r,g):
        if g!=self.recommend_generation or self.encoding:return
        self.recommendations[key]=r;button=self.profile_buttons[key];button.setEnabled(r['found'])
        button.setToolTip(r['detail']+'\n'+(f'{r["height"]}p · ≈ {r["mb"]:.0f} МБ · VMAF {r["vmaf"]:.1f}' if r['found'] else r['reason']))
        if r['found']:self.quality.markers[key]=dict(level=quality_for_crf(r['crf']),name=r['name'])
        else:self.quality.markers.pop(key,None)
        self.quality.update()
        if r['found'] and key==self.selected_profile and self.recommend_apply and self.target_revision==self.recommend_revision:
            self.apply_recommendation(key)
    def recommended(self,r,g):
        if g!=self.recommend_generation:return
        self.recommend_worker=None;self.rec_button.setEnabled(True)
        if r.get('cancelled'):
            self.rec_progress.setText('Расчёт отменён. Нажмите «Пересчитать рекомендации».');return
        for key,item in r['profiles'].items():self.profile_ready(key,item,g)
        found=sum(bool(item['found']) for item in self.recommendations.values())
        self.rec_progress.setText(f'Анализ завершён: доступно рекомендаций {found}/3.')
        if not self.rec:
            self.rec_label.setText('Нажмите метку или кнопку профиля, чтобы применить рекомендацию.' if found else self.recommendations['maximum']['reason'])
        self.update_labels()
        if not self.items and not self.current and not self.preview_timer.isActive():self.start_preview()
    def start_preview(self):
        if not self.media or self.encoding:return
        if self.current:self.current.cancel()
        self.generation+=1;self.rec_button.setEnabled(True);g=self.generation
        folder=Path(self.temp.name)/str(g);folder.mkdir(exist_ok=True)
        w=PreviewWorker(self.job(),folder,self,source_cache=self.source_cache);self.current=w
        w.status.connect(lambda s:self.set_status(s,g));w.done.connect(lambda r:self.preview_ready(r,g));w.failed.connect(lambda e:self.error(e,g));self.retain(w)
    def set_status(self,s,g):
        if g==self.generation:self.status.setText(s)
    def preview_ready(self,r,g):
        if g!=self.generation or r.get('cancelled'):return
        self.current=None;self.items=r['items'];self.estimated=r['mb'];self.preview_pending=False
        for card,item in zip(self.cards,self.items):card.image=QPixmap(item['compressed']);card.caption=clock(item['time']);card.update()
        self.status.setText('Превью готово'+(' (из кэша)' if r.get('cached') else '')+'. Нажмите на кадр для сравнения.');self.update_labels()
    def open_compare(self,n):
        if self.preview_pending or n>=len(self.items):return
        dialog=Comparison(self.items[n],self.compare_combo.currentData(),self.fit_check.isChecked(),self)
        self.dialogs.append(dialog);dialog.preferences.connect(self.compare_preferences)
        dialog.destroyed.connect(lambda:self.dialogs.remove(dialog) if dialog in self.dialogs else None);dialog.show()
    def compare_preferences(self,mode,fit):
        self.loading=True;self.set_combo(self.compare_combo,mode);self.fit_check.setChecked(fit);self.loading=False;self.persist()
    def error(self,e,g):
        if g!=self.generation:return
        self.current=None;self.rec_button.setEnabled(bool(self.media));self.status.setText('Ошибка: '+e)
    def start_encoding(self):
        if self.encoding:
            self.encoding.cancel();self.go.setEnabled(False);self.progress_label.setText('Отмена…');return
        if not self.media:return
        job=self.job()
        if job.engine=='gpu' and ('hevc' if job.codec=='H.265 / HEVC' else 'h264') not in self.gpu:
            QMessageBox.warning(self,'GPU недоступен','Выберите обработку «Авто» или CPU.');return
        if job.method=='size' and job.target_mb*1024**2*8/job.media.duration/1000<=job.audio_rate+32:
            QMessageBox.warning(self,'Слишком маленький размер','Целевой размер меньше, чем требуется для выбранного звука. Увеличьте его или уменьшите битрейт аудио.');return
        self.resume_recommendation=bool(self.recommend_worker) or self.recommend_timer.isActive()
        self.cancel_recommendation()
        if self.resume_recommendation:self.rec_progress.setText('Расчёт рекомендаций продолжится после кодирования.')
        if self.current:self.current.cancel();self.current=None
        self.preview_timer.stop();self.generation+=1;self.persist()
        folder=Path(self.output_edit.text() or Path(self.media.path).parent)
        try:folder.mkdir(parents=True,exist_ok=True)
        except OSError as e:QMessageBox.warning(self,'Папка результата',str(e));return
        suffix='.'+self.container.currentData();base=Path(self.media.path).stem+'_сжато'
        output=folder/(base+suffix);n=2
        while output.exists() or output.with_name(output.stem+'.partial'+output.suffix).exists():output=folder/(base+f'_{n}'+suffix);n+=1
        w=EncodeWorker(job,output,self);self.encoding=w;self.controls(False);self.pick.setEnabled(False)
        self.go.setEnabled(True);self.go.setText('ОТМЕНА');self.progress.setValue(0)
        self.status.setText('Кодирование: '+job.encoder+' · '+('NVIDIA GPU (NVENC; смотрите Video Encode, а не общий процент GPU)' if job.engine=='gpu' else 'CPU')+' → '+str(output))
        w.progress.connect(lambda p,s:(self.progress.setValue(round(p*1000)),self.progress_label.setText(s)))
        w.done.connect(self.encoded);w.failed.connect(self.encode_failed);self.retain(w)
    def encoded(self,r):
        self.encoding=None;self.controls(True);self.pick.setEnabled(True);self.go.setText('СЖАТЬ ВИДЕО')
        if r.get('cancelled'):self.status.setText('Отменено. Исходник сохранён.');self.progress.setValue(0);self.progress_label.clear()
        else:
            self.last_output=r['path'];self.update_result_actions();self.persist()
            self.status.setText(f'Готово: {r["path"]} · {r["mb"]:.0f} МБ');self.progress_label.setText('Готово')
        if self.resume_recommendation and not self.closing:self.recommend_timer.start()
    def encode_failed(self,e):
        self.encoding=None;self.controls(True);self.pick.setEnabled(True);self.go.setText('СЖАТЬ ВИДЕО');self.status.setText('Ошибка кодирования: '+e)
        QMessageBox.warning(self,'Кодирование не завершено',e)
        if self.resume_recommendation and not self.closing:self.recommend_timer.start()
    def dragEnterEvent(self,e):
        if e.mimeData().hasUrls() and not self.encoding:e.acceptProposedAction()
    def dropEvent(self,e):
        urls=e.mimeData().urls()
        if urls:self.load_file(urls[0].toLocalFile())
    def closeEvent(self,e):
        self.persist();self.preview_timer.stop();self.save_timer.stop();self.cancel_recommendation()
        if self.workers:
            self.closing=True
            for w in list(self.workers):w.cancel()
            self.hide();e.ignore();return
        self.temp.cleanup();e.accept()
