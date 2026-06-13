"""Exam management workspaces for exams, papers, rooms and seating plans."""
from __future__ import annotations
import sqlite3, tkinter as tk
from tkinter import filedialog, messagebox, ttk
import auth
from config import DB_PATH, SPLASH_BG
from ui_workspace import WorkspacePage
from exam_service import (EXAM_TYPES, PAPER_STATUSES, add_exam_subject, create_exam, generate_seating_plan,
    install_exam_schema, list_exam_subjects, list_exams, list_rooms, list_seating_assignments, update_paper, upsert_room)


def _connect():
    conn=sqlite3.connect(DB_PATH); conn.row_factory=sqlite3.Row; conn.execute('PRAGMA foreign_keys=ON'); install_exam_schema(conn); return conn

class ExamWindow(WorkspacePage):
    """Create exams, print/store papers, configure room spaces and generate seating."""
    @auth.require_permission('manage_exams')
    def __init__(self, master=None, embedded=False, initial_tab='exams'):
        super().__init__(master, embedded=embedded); self.title('Exam Management'); self.configure(bg=SPLASH_BG); self.exam_options={}; self.subject_options={}; self.room_options={}; self.plan_id=None
        ttk.Label(self,text='Complete Exam Management',font=('Segoe UI',18,'bold')).pack(anchor='w',padx=16,pady=10)
        self.nb=ttk.Notebook(self); self.nb.pack(fill='both',expand=True,padx=16,pady=8)
        self._build_exams(); self._build_papers(); self._build_rooms(); self._build_seating(); self.refresh()
        tab_map={'exams':0,'papers':1,'rooms':2,'seating':3}; self.nb.select(tab_map.get(initial_tab,0))
    def _tab(self,text):
        f=ttk.Frame(self.nb,padding=10); self.nb.add(f,text=text); return f
    def _combo(self,parent,var,row,label):
        ttk.Label(parent,text=label).grid(row=row,column=0,sticky='w',pady=4); c=ttk.Combobox(parent,textvariable=var,state='readonly',width=45); c.grid(row=row,column=1,sticky='ew',padx=8,pady=4); return c
    def _entry(self,parent,var,row,label,width=35):
        ttk.Label(parent,text=label).grid(row=row,column=0,sticky='w',pady=4); ttk.Entry(parent,textvariable=var,width=width).grid(row=row,column=1,sticky='ew',padx=8,pady=4)
    def _build_exams(self):
        f=self._tab('Create All Exams'); self.exam_name=tk.StringVar(); self.exam_type=tk.StringVar(value=EXAM_TYPES[0]); self.year=tk.StringVar(value='2025-26'); self.starts=tk.StringVar(); self.ends=tk.StringVar()
        self._entry(f,self.exam_name,0,'Exam Name'); ttk.Label(f,text='Exam Type').grid(row=1,column=0,sticky='w'); ttk.Combobox(f,textvariable=self.exam_type,values=EXAM_TYPES,state='readonly').grid(row=1,column=1,sticky='w',padx=8)
        self._entry(f,self.year,2,'Academic Year'); self._entry(f,self.starts,3,'Starts On'); self._entry(f,self.ends,4,'Ends On')
        ttk.Button(f,text='Create Exam',command=self.save_exam,style='Accent.TButton').grid(row=5,column=1,sticky='w',pady=8)
        self.exam_tree=ttk.Treeview(f,columns=('id','name','type','year','dates'),show='headings',height=12); [self.exam_tree.heading(c,text=c.title()) for c in ('id','name','type','year','dates')]; self.exam_tree.grid(row=6,column=0,columnspan=3,sticky='nsew')
    def _build_papers(self):
        f=self._tab('Paper Printing & Storage'); self.paper_exam=tk.StringVar(); self.paper_class=tk.StringVar(); self.paper_subject=tk.StringVar(); self.paper_max=tk.StringVar(value='100'); self.paper_date=tk.StringVar(); self.paper_status=tk.StringVar(value='DRAFT'); self.paper_file=tk.StringVar(); self.paper_store=tk.StringVar()
        self.paper_exam_combo=self._combo(f,self.paper_exam,0,'Exam'); self._entry(f,self.paper_class,1,'Class'); self._entry(f,self.paper_subject,2,'Subject'); self._entry(f,self.paper_max,3,'Max Marks'); self._entry(f,self.paper_date,4,'Exam Date')
        ttk.Label(f,text='Paper Status').grid(row=5,column=0,sticky='w'); ttk.Combobox(f,textvariable=self.paper_status,values=PAPER_STATUSES,state='readonly').grid(row=5,column=1,sticky='w',padx=8)
        self._entry(f,self.paper_file,6,'Paper File'); ttk.Button(f,text='Browse Paper',command=self.browse_paper).grid(row=6,column=2,sticky='w')
        self._entry(f,self.paper_store,7,'Stored Location'); ttk.Button(f,text='Save Subject / Paper',command=self.save_subject,style='Accent.TButton').grid(row=8,column=1,sticky='w',pady=8)
        self.subject_tree=ttk.Treeview(f,columns=('id','exam','class','subject','max','status','file','store'),show='headings',height=10); [self.subject_tree.heading(c,text=c.title()) for c in ('id','exam','class','subject','max','status','file','store')]; self.subject_tree.grid(row=9,column=0,columnspan=3,sticky='nsew'); self.subject_tree.bind('<<TreeviewSelect>>',self.pick_subject)
    def _build_rooms(self):
        f=self._tab('Classroom Spaces'); self.room_name=tk.StringVar(); self.rows=tk.StringVar(value='5'); self.cols=tk.StringVar(value='4')
        self._entry(f,self.room_name,0,'Room/Class Name'); self._entry(f,self.rows,1,'Rows'); self._entry(f,self.cols,2,'Columns')
        ttk.Label(f,text='Capacity assumes 2 students per bench/cell. Each room may have separate rows and columns.').grid(row=3,column=0,columnspan=3,sticky='w',pady=5)
        ttk.Button(f,text='Save Room Space',command=self.save_room,style='Accent.TButton').grid(row=4,column=1,sticky='w',pady=8)
        self.room_tree=ttk.Treeview(f,columns=('id','name','rows','cols','capacity'),show='headings',height=12); [self.room_tree.heading(c,text=c.title()) for c in ('id','name','rows','cols','capacity')]; self.room_tree.grid(row=5,column=0,columnspan=3,sticky='nsew')
    def _build_seating(self):
        f=self._tab('Exam Seating Plan'); self.seat_exam=tk.StringVar(); self.seat_name=tk.StringVar(value='Main Seating'); self.seat_classes=tk.StringVar(); self.seat_rooms=tk.StringVar()
        self.seat_exam_combo=self._combo(f,self.seat_exam,0,'Exam'); self._entry(f,self.seat_name,1,'Plan Name'); self._entry(f,self.seat_classes,2,'Classes (comma separated)'); self._entry(f,self.seat_rooms,3,'Room IDs (comma separated)')
        ttk.Label(f,text='Rule: two students on one bench are from different classes; students behind keep the same class pattern.').grid(row=4,column=0,columnspan=3,sticky='w',pady=4)
        ttk.Button(f,text='Generate Seating Plan',command=self.save_seating,style='Accent.TButton').grid(row=5,column=1,sticky='w',pady=8)
        self.seat_tree=ttk.Treeview(f,columns=('room','row','col','pos','student','class'),show='headings',height=14); [self.seat_tree.heading(c,text=c.title()) for c in ('room','row','col','pos','student','class')]; self.seat_tree.grid(row=6,column=0,columnspan=3,sticky='nsew')
    def refresh(self):
        with _connect() as c:
            exams=list_exams(c); self.exam_options={f"#{r['id']} — {r['name']} ({r['academic_year']})":r['id'] for r in exams}
            for combo in (self.paper_exam_combo,self.seat_exam_combo): combo.configure(values=tuple(self.exam_options)); combo.set(next(iter(self.exam_options),''))
            self.exam_tree.delete(*self.exam_tree.get_children()); [self.exam_tree.insert('', 'end', values=(r['id'],r['name'],r['exam_type'],r['academic_year'],f"{r['starts_on'] or ''} {r['ends_on'] or ''}")) for r in exams]
            subjects=list_exam_subjects(c); self.subject_options={f"#{r['id']} — {r['class_name']} {r['subject_name']}":r['id'] for r in subjects}; self.subject_tree.delete(*self.subject_tree.get_children()); [self.subject_tree.insert('', 'end', iid=str(r['id']), values=(r['id'],r['exam_name'],r['class_name'],r['subject_name'],r['max_marks'],r['paper_status'],r['paper_file'] or '',r['stored_location'] or '')) for r in subjects]
            self.room_tree.delete(*self.room_tree.get_children()); [self.room_tree.insert('', 'end', values=(r['id'],r['name'],r['rows_count'],r['columns_count'],r['capacity'])) for r in list_rooms(c)]
    def _exam_id(self,var): return self.exam_options.get(var.get()) or int(str(var.get()).split()[0].lstrip('#'))
    def browse_paper(self):
        path=filedialog.askopenfilename(parent=self,filetypes=(('Documents','*.pdf *.doc *.docx *.png *.jpg'),('All files','*.*')))
        if path: self.paper_file.set(path)
    def pick_subject(self,_=None):
        sel=self.subject_tree.selection();
        if not sel: return
        vals=self.subject_tree.item(sel[0],'values'); self.paper_class.set(vals[2]); self.paper_subject.set(vals[3]); self.paper_max.set(vals[4]); self.paper_status.set(vals[5]); self.paper_file.set(vals[6]); self.paper_store.set(vals[7])
    def save_exam(self):
        try:
            with _connect() as c: create_exam(c,self.exam_name.get(),self.exam_type.get(),self.year.get(),self.starts.get(),self.ends.get()); c.commit()
            self.refresh()
        except Exception as e: messagebox.showerror('Exam',str(e),parent=self)
    def save_subject(self):
        try:
            with _connect() as c:
                sid=add_exam_subject(c,self._exam_id(self.paper_exam),self.paper_class.get(),self.paper_subject.get(),float(self.paper_max.get()),self.paper_date.get()); update_paper(c,sid,self.paper_status.get(),self.paper_file.get(),self.paper_store.get()); c.commit()
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
            with _connect() as c:
                pid=generate_seating_plan(c,self._exam_id(self.seat_exam),self.seat_name.get(),classes,rooms); rows=list_seating_assignments(c,pid); c.commit()
            self.seat_tree.delete(*self.seat_tree.get_children()); [self.seat_tree.insert('', 'end', values=(r['room_name'],r['row_no'],r['column_no'],r['bench_position'],r['student_name'],r['class_name'])) for r in rows]
            messagebox.showinfo('Seating',f'Created seating plan #{pid}',parent=self)
        except Exception as e: messagebox.showerror('Seating',str(e),parent=self)
