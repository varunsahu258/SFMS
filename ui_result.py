"""Result management workspace for marks, grades, marksheets, and PTM diaries."""
from __future__ import annotations
import sqlite3, tkinter as tk
from tkinter import messagebox, ttk
import auth
from config import DB_PATH, SPLASH_BG
from ui_workspace import WorkspacePage
from exam_service import (NON_SCHOLASTIC, PERSONAL_QUALITY, install_exam_schema, list_exam_subjects, list_exams, list_students,
    marksheet_pdf, result_diary_pdf, save_marks, save_personality_grade)

def _connect():
    conn=sqlite3.connect(DB_PATH); conn.row_factory=sqlite3.Row; conn.execute('PRAGMA foreign_keys=ON'); install_exam_schema(conn); return conn

class ResultWindow(WorkspacePage):
    """Enter scholastic marks, personality grades and print report cards/diaries."""
    @auth.require_permission('manage_results')
    def __init__(self, master=None, embedded=False, initial_tab='marks'):
        super().__init__(master, embedded=embedded); self.title('Result Management'); self.configure(bg=SPLASH_BG); self.exam_options={}; self.subject_options={}; self.student_options={}
        ttk.Label(self,text='Complete Result Management',font=('Segoe UI',18,'bold')).pack(anchor='w',padx=16,pady=10)
        self.nb=ttk.Notebook(self); self.nb.pack(fill='both',expand=True,padx=16,pady=8)
        self._marks(); self._personality(); self._prints(); self.refresh(); self.nb.select({'marks':0,'personality':1,'marksheet':2}.get(initial_tab,0))
    def _tab(self,text): f=ttk.Frame(self.nb,padding=10); self.nb.add(f,text=text); return f
    def _combo(self,parent,var,row,label): ttk.Label(parent,text=label).grid(row=row,column=0,sticky='w',pady=4); c=ttk.Combobox(parent,textvariable=var,state='readonly',width=52); c.grid(row=row,column=1,sticky='ew',padx=8,pady=4); return c
    def _entry(self,parent,var,row,label): ttk.Label(parent,text=label).grid(row=row,column=0,sticky='w',pady=4); ttk.Entry(parent,textvariable=var,width=24).grid(row=row,column=1,sticky='w',padx=8,pady=4)
    def _marks(self):
        f=self._tab('Marks Entry'); self.exam_subject=tk.StringVar(); self.student=tk.StringVar(); self.monthly=tk.StringVar(value='0'); self.half=tk.StringVar(value='0'); self.project=tk.StringVar(value='0'); self.annual=tk.StringVar(value='0'); self.grade=tk.StringVar(); self.remarks=tk.StringVar()
        self.subject_combo=self._combo(f,self.exam_subject,0,'Exam Subject'); self.student_combo=self._combo(f,self.student,1,'Student')
        for i,(lbl,var) in enumerate((('Monthly Test (10)',self.monthly),('Half Yearly (20)',self.half),('Project Work (10)',self.project),('Annual Exam (60)',self.annual),('Grade Override',self.grade),('Remarks',self.remarks)),2): self._entry(f,var,i,lbl)
        ttk.Button(f,text='Save Marks / Grade',command=self.save_marks,style='Accent.TButton').grid(row=8,column=1,sticky='w',pady=8)
        self.marks_tree=ttk.Treeview(f,columns=('subject_id','student_id','student','class'),show='headings',height=10); [self.marks_tree.heading(c,text=c.title()) for c in ('subject_id','student_id','student','class')]; self.marks_tree.grid(row=9,column=0,columnspan=3,sticky='nsew')
    def _personality(self):
        f=self._tab('Personality / Co-Scholastic Grades'); self.pg_exam=tk.StringVar(); self.pg_student=tk.StringVar(); self.pg_category=tk.StringVar(value='Non Scholastic achievement'); self.pg_indicator=tk.StringVar(); self.term1=tk.StringVar(); self.term2=tk.StringVar()
        self.pg_exam_combo=self._combo(f,self.pg_exam,0,'Exam'); self.pg_student_combo=self._combo(f,self.pg_student,1,'Student')
        ttk.Label(f,text='Category').grid(row=2,column=0,sticky='w'); ttk.Combobox(f,textvariable=self.pg_category,values=('Non Scholastic achievement','Personal Quality'),state='readonly').grid(row=2,column=1,sticky='w',padx=8)
        ttk.Label(f,text='Indicator').grid(row=3,column=0,sticky='w'); ttk.Combobox(f,textvariable=self.pg_indicator,values=NON_SCHOLASTIC+PERSONAL_QUALITY).grid(row=3,column=1,sticky='w',padx=8)
        self._entry(f,self.term1,4,'Term 1 Grade'); self._entry(f,self.term2,5,'Term 2 Grade')
        ttk.Button(f,text='Save Personality Grade',command=self.save_personality,style='Accent.TButton').grid(row=6,column=1,sticky='w',pady=8)
        ttk.Button(f,text='Fill Template Indicators A/B',command=self.fill_template_grades).grid(row=7,column=1,sticky='w')
    def _prints(self):
        f=self._tab('Marksheet Generation & PTM Diary'); self.print_exam=tk.StringVar(); self.print_student=tk.StringVar(); self.diary_class=tk.StringVar()
        self.print_exam_combo=self._combo(f,self.print_exam,0,'Exam'); self.print_student_combo=self._combo(f,self.print_student,1,'Student')
        self._entry(f,self.diary_class,2,'Class for Result Diary')
        ttk.Button(f,text='Print Student Marksheet',command=self.print_marksheet,style='Accent.TButton').grid(row=3,column=1,sticky='w',pady=8)
        ttk.Button(f,text='Generate PTM Result Diary',command=self.print_diary).grid(row=4,column=1,sticky='w',pady=8)
    def refresh(self):
        with _connect() as c:
            exams=list_exams(c); self.exam_options={f"#{r['id']} — {r['name']} ({r['academic_year']})":r['id'] for r in exams}
            students=list_students(c); self.student_options={f"#{r['id']} — {r['name']} ({r.get('class') or ''})":r['id'] for r in students}
            subjects=list_exam_subjects(c); self.subject_options={f"#{r['id']} — {r['exam_name']} / {r['class_name']} / {r['subject_name']}":r['id'] for r in subjects}
        for combo, opts in ((self.subject_combo,self.subject_options),(self.student_combo,self.student_options),(self.pg_exam_combo,self.exam_options),(self.pg_student_combo,self.student_options),(self.print_exam_combo,self.exam_options),(self.print_student_combo,self.student_options)):
            combo.configure(values=tuple(opts)); combo.set(next(iter(opts),''))
        self.marks_tree.delete(*self.marks_tree.get_children()); [self.marks_tree.insert('', 'end', values=(sid, stid, label, label.split('(')[-1].rstrip(')'))) for label,stid in self.student_options.items() for _k,sid in list(self.subject_options.items())[:1]]
    def _id(self,var,opts): return opts.get(var.get()) or int(str(var.get()).split()[0].lstrip('#'))
    def save_marks(self):
        try:
            with _connect() as c: save_marks(c,self._id(self.exam_subject,self.subject_options),self._id(self.student,self.student_options),self.monthly.get(),self.half.get(),self.project.get(),self.annual.get(),self.grade.get(),self.remarks.get()); c.commit()
            messagebox.showinfo('Result','Marks saved and grade calculated.',parent=self)
        except Exception as e: messagebox.showerror('Result',str(e),parent=self)
    def save_personality(self):
        try:
            with _connect() as c: save_personality_grade(c,self._id(self.pg_exam,self.exam_options),self._id(self.pg_student,self.student_options),self.pg_category.get(),self.pg_indicator.get(),self.term1.get(),self.term2.get()); c.commit()
            messagebox.showinfo('Personality Grade','Grade saved.',parent=self)
        except Exception as e: messagebox.showerror('Personality Grade',str(e),parent=self)
    def fill_template_grades(self):
        try:
            with _connect() as c:
                exam_id=self._id(self.pg_exam,self.exam_options); student_id=self._id(self.pg_student,self.student_options)
                for name in NON_SCHOLASTIC: save_personality_grade(c,exam_id,student_id,'Non Scholastic achievement',name,'A','A')
                for name in PERSONAL_QUALITY: save_personality_grade(c,exam_id,student_id,'Personal Quality',name,'A','A')
                c.commit()
            messagebox.showinfo('Template Grades','Default A/A template grades filled.',parent=self)
        except Exception as e: messagebox.showerror('Template Grades',str(e),parent=self)
    def print_marksheet(self):
        try:
            with _connect() as c: path=marksheet_pdf(c,self._id(self.print_exam,self.exam_options),self._id(self.print_student,self.student_options))
            messagebox.showinfo('Marksheet',f'Saved to:\n{path}',parent=self)
        except Exception as e: messagebox.showerror('Marksheet',str(e),parent=self)
    def print_diary(self):
        try:
            with _connect() as c: path=result_diary_pdf(c,self._id(self.print_exam,self.exam_options),self.diary_class.get())
            messagebox.showinfo('Result Diary',f'Saved to:\n{path}',parent=self)
        except Exception as e: messagebox.showerror('Result Diary',str(e),parent=self)
