"""Result management workspace for marks entry, marksheets, and PTM diaries."""
from __future__ import annotations
import sqlite3, tkinter as tk
from tkinter import ttk, messagebox
import auth
from config import DB_PATH, SPLASH_BG
from ui_workspace import WorkspacePage
from exam_service import install_exam_schema, save_marks, marksheet_pdf, result_diary_pdf

def _connect():
    conn=sqlite3.connect(DB_PATH); conn.row_factory=sqlite3.Row; conn.execute('PRAGMA foreign_keys=ON'); install_exam_schema(conn); return conn

class ResultWindow(WorkspacePage):
    """Enter marks/grades and print report-card-style marksheets and PTM diaries."""
    @auth.require_permission('manage_results')
    def __init__(self, master=None, embedded=False):
        super().__init__(master, embedded=embedded); self.title('Result Management'); self.configure(bg=SPLASH_BG)
        ttk.Label(self,text='Result Management',font=('Segoe UI',18,'bold')).pack(anchor='w',padx=16,pady=10)
        nb=ttk.Notebook(self); nb.pack(fill='both',expand=True,padx=16,pady=8); self._marks(nb); self._prints(nb); self.refresh()
    def _marks(self, nb):
        f=ttk.Frame(nb,padding=10); nb.add(f,text='Marks & Grades Entry')
        self.exam_subject_id=tk.StringVar(); self.student_id=tk.StringVar(); self.monthly=tk.StringVar(value='0'); self.half=tk.StringVar(value='0'); self.project=tk.StringVar(value='0'); self.annual=tk.StringVar(value='0'); self.grade=tk.StringVar()
        for i,(lbl,var) in enumerate((('Exam Subject ID',self.exam_subject_id),('Student ID',self.student_id),('Monthly Test',self.monthly),('Half Yearly',self.half),('Project Work',self.project),('Annual Exam',self.annual),('Grade Override',self.grade))): ttk.Label(f,text=lbl).grid(row=i,column=0,sticky='w'); ttk.Entry(f,textvariable=var,width=25).grid(row=i,column=1,sticky='w')
        ttk.Button(f,text='Save Marks / Grade',command=self.save).grid(row=7,column=1,sticky='w',pady=8)
        self.subject_tree=ttk.Treeview(f,columns=('id','exam','class','subject','max'),show='headings',height=8); [self.subject_tree.heading(c,text=c.title()) for c in ('id','exam','class','subject','max')]; self.subject_tree.grid(row=8,column=0,columnspan=3,sticky='nsew',pady=8)
        self.student_tree=ttk.Treeview(f,columns=('id','scholar','name','class'),show='headings',height=8); [self.student_tree.heading(c,text=c.title()) for c in ('id','scholar','name','class')]; self.student_tree.grid(row=9,column=0,columnspan=3,sticky='nsew')
    def _prints(self, nb):
        f=ttk.Frame(nb,padding=10); nb.add(f,text='Marksheets & PTM Diary')
        self.print_exam=tk.StringVar(); self.print_student=tk.StringVar(); self.diary_class=tk.StringVar()
        for i,(lbl,var) in enumerate((('Exam ID',self.print_exam),('Student ID',self.print_student),('Class for Result Diary',self.diary_class))): ttk.Label(f,text=lbl).grid(row=i,column=0,sticky='w'); ttk.Entry(f,textvariable=var,width=28).grid(row=i,column=1,sticky='w')
        ttk.Button(f,text='Print Student Marksheet',command=self.print_marksheet).grid(row=3,column=1,sticky='w',pady=8)
        ttk.Button(f,text='Generate PTM Result Diary',command=self.print_diary).grid(row=4,column=1,sticky='w',pady=8)
    def refresh(self):
        with _connect() as c:
            self.subject_tree.delete(*self.subject_tree.get_children()); [self.subject_tree.insert('', 'end', values=(r['id'],r['exam_name'],r['class_name'],r['subject_name'],r['max_marks'])) for r in c.execute('SELECT es.*,e.name exam_name FROM exam_subjects es JOIN exams e ON e.id=es.exam_id ORDER BY e.id DESC,es.class_name')]
            self.student_tree.delete(*self.student_tree.get_children()); [self.student_tree.insert('', 'end', values=(r['id'],r['scholar_no'],r['name'],r['class'])) for r in c.execute('SELECT id,scholar_no,name,class FROM students ORDER BY class,name LIMIT 300')]
    def save(self):
        try:
            with _connect() as c: save_marks(c,int(self.exam_subject_id.get()),int(self.student_id.get()),self.monthly.get(),self.half.get(),self.project.get(),self.annual.get(),self.grade.get()); c.commit()
            messagebox.showinfo('Result','Marks saved.',parent=self)
        except Exception as e: messagebox.showerror('Result',str(e),parent=self)
    def print_marksheet(self):
        try:
            with _connect() as c: path=marksheet_pdf(c,int(self.print_exam.get()),int(self.print_student.get()))
            messagebox.showinfo('Marksheet',f'Saved to:\n{path}',parent=self)
        except Exception as e: messagebox.showerror('Marksheet',str(e),parent=self)
    def print_diary(self):
        try:
            with _connect() as c: path=result_diary_pdf(c,int(self.print_exam.get()),self.diary_class.get())
            messagebox.showinfo('Result Diary',f'Saved to:\n{path}',parent=self)
        except Exception as e: messagebox.showerror('Result Diary',str(e),parent=self)
