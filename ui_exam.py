"""Exam management workspace for exams, papers, and seating plans."""
from __future__ import annotations
import sqlite3, tkinter as tk
from tkinter import ttk, messagebox
import auth
from config import DB_PATH, SPLASH_BG
from ui_workspace import WorkspacePage
from exam_service import EXAM_TYPES, add_exam_subject, create_exam, generate_seating_plan, install_exam_schema, upsert_room


def _connect():
    conn=sqlite3.connect(DB_PATH); conn.row_factory=sqlite3.Row; conn.execute('PRAGMA foreign_keys=ON'); install_exam_schema(conn); return conn

class ExamWindow(WorkspacePage):
    """Create exams, register papers, store paper status, and build seating plans."""
    @auth.require_permission('manage_exams')
    def __init__(self, master=None, embedded=False):
        super().__init__(master, embedded=embedded); self.title('Exam Management'); self.configure(bg=SPLASH_BG)
        ttk.Label(self,text='Exam Management',font=('Segoe UI',18,'bold')).pack(anchor='w',padx=16,pady=10)
        nb=ttk.Notebook(self); nb.pack(fill='both',expand=True,padx=16,pady=8)
        self._build_exams(nb); self._build_subjects(nb); self._build_rooms(nb); self._build_seating(nb); self.refresh()
    def _tab(self, nb, text):
        f=ttk.Frame(nb,padding=10); nb.add(f,text=text); return f
    def _build_exams(self, nb):
        f=self._tab(nb,'Create Exams'); self.exam_name=tk.StringVar(); self.exam_type=tk.StringVar(value=EXAM_TYPES[0]); self.year=tk.StringVar(value='2025-26')
        for i,(lbl,var) in enumerate((('Exam Name',self.exam_name),('Academic Year',self.year))): ttk.Label(f,text=lbl).grid(row=i,column=0,sticky='w'); ttk.Entry(f,textvariable=var,width=30).grid(row=i,column=1,sticky='w')
        ttk.Label(f,text='Exam Type').grid(row=2,column=0,sticky='w'); ttk.Combobox(f,textvariable=self.exam_type,values=EXAM_TYPES,state='readonly').grid(row=2,column=1,sticky='w')
        ttk.Button(f,text='Create Exam',command=self.save_exam).grid(row=3,column=1,sticky='w',pady=8)
        self.exam_tree=ttk.Treeview(f,columns=('id','name','type','year'),show='headings',height=12); [self.exam_tree.heading(c,text=c.title()) for c in ('id','name','type','year')]; self.exam_tree.grid(row=4,column=0,columnspan=3,sticky='nsew')
    def _build_subjects(self, nb):
        f=self._tab(nb,'Exam Timetable & Papers'); self.subject_exam=tk.StringVar(); self.subject_class=tk.StringVar(); self.subject_name=tk.StringVar(); self.subject_max=tk.StringVar(value='100'); self.paper_status=tk.StringVar(value='DRAFT')
        for i,(lbl,var) in enumerate((('Exam',self.subject_exam),('Class',self.subject_class),('Subject',self.subject_name),('Max Marks',self.subject_max),('Paper Status',self.paper_status))): ttk.Label(f,text=lbl).grid(row=i,column=0,sticky='w'); ttk.Entry(f,textvariable=var,width=34).grid(row=i,column=1,sticky='w')
        ttk.Button(f,text='Save Subject / Paper',command=self.save_subject).grid(row=5,column=1,sticky='w',pady=8)
        self.subject_tree=ttk.Treeview(f,columns=('exam','class','subject','max','status'),show='headings',height=12); [self.subject_tree.heading(c,text=c.title()) for c in ('exam','class','subject','max','status')]; self.subject_tree.grid(row=6,column=0,columnspan=3,sticky='nsew')
    def _build_rooms(self, nb):
        f=self._tab(nb,'Classroom Spaces'); self.room_name=tk.StringVar(); self.rows=tk.StringVar(value='5'); self.cols=tk.StringVar(value='4')
        for i,(lbl,var) in enumerate((('Room/Class Name',self.room_name),('Rows',self.rows),('Columns',self.cols))): ttk.Label(f,text=lbl).grid(row=i,column=0,sticky='w'); ttk.Entry(f,textvariable=var,width=24).grid(row=i,column=1,sticky='w')
        ttk.Button(f,text='Save Room Space',command=self.save_room).grid(row=3,column=1,sticky='w',pady=8)
        self.room_tree=ttk.Treeview(f,columns=('id','name','rows','cols','capacity'),show='headings',height=12); [self.room_tree.heading(c,text=c.title()) for c in ('id','name','rows','cols','capacity')]; self.room_tree.grid(row=4,column=0,columnspan=3,sticky='nsew')
    def _build_seating(self, nb):
        f=self._tab(nb,'Seating Plan'); self.seat_exam=tk.StringVar(); self.seat_name=tk.StringVar(value='Main Seating'); self.seat_classes=tk.StringVar(); self.seat_rooms=tk.StringVar()
        help='Classes comma-separated; Room IDs comma-separated. Two students share each bench from different classes; column pattern keeps students behind from the same class.'
        ttk.Label(f,text=help,wraplength=760).grid(row=0,column=0,columnspan=2,sticky='w')
        for i,(lbl,var) in enumerate((('Exam ID',self.seat_exam),('Plan Name',self.seat_name),('Classes',self.seat_classes),('Room IDs',self.seat_rooms)),1): ttk.Label(f,text=lbl).grid(row=i,column=0,sticky='w'); ttk.Entry(f,textvariable=var,width=40).grid(row=i,column=1,sticky='w')
        ttk.Button(f,text='Generate Seating Plan',command=self.save_seating).grid(row=5,column=1,sticky='w',pady=8)
    def refresh(self):
        with _connect() as c:
            self.exam_tree.delete(*self.exam_tree.get_children()); [self.exam_tree.insert('', 'end', values=(r['id'],r['name'],r['exam_type'],r['academic_year'])) for r in c.execute('SELECT * FROM exams ORDER BY id DESC')]
            self.subject_tree.delete(*self.subject_tree.get_children()); [self.subject_tree.insert('', 'end', values=(r['exam_name'],r['class_name'],r['subject_name'],r['max_marks'],r['paper_status'])) for r in c.execute('SELECT es.*,e.name exam_name FROM exam_subjects es JOIN exams e ON e.id=es.exam_id ORDER BY e.id DESC,es.class_name')]
            self.room_tree.delete(*self.room_tree.get_children()); [self.room_tree.insert('', 'end', values=(r['id'],r['name'],r['rows_count'],r['columns_count'],r['capacity'])) for r in c.execute('SELECT * FROM exam_rooms ORDER BY name')]
    def save_exam(self):
        try:
            with _connect() as c: create_exam(c,self.exam_name.get(),self.exam_type.get(),self.year.get()); c.commit()
            self.refresh()
        except Exception as e: messagebox.showerror('Exam',str(e),parent=self)
    def save_subject(self):
        try:
            with _connect() as c: sid=add_exam_subject(c,int(self.subject_exam.get()),self.subject_class.get(),self.subject_name.get(),float(self.subject_max.get())); c.execute('UPDATE exam_subjects SET paper_status=? WHERE id=?',(self.paper_status.get(),sid)); c.commit()
            self.refresh()
        except Exception as e: messagebox.showerror('Paper',str(e),parent=self)
    def save_room(self):
        try:
            with _connect() as c: upsert_room(c,self.room_name.get(),int(self.rows.get()),int(self.cols.get())); c.commit()
            self.refresh()
        except Exception as e: messagebox.showerror('Room',str(e),parent=self)
    def save_seating(self):
        try:
            classes=[x.strip() for x in self.seat_classes.get().split(',') if x.strip()]; rooms=[int(x) for x in self.seat_rooms.get().split(',') if x.strip()]
            with _connect() as c: pid=generate_seating_plan(c,int(self.seat_exam.get()),self.seat_name.get(),classes,rooms); c.commit()
            messagebox.showinfo('Seating',f'Created seating plan #{pid}',parent=self)
        except Exception as e: messagebox.showerror('Seating',str(e),parent=self)
