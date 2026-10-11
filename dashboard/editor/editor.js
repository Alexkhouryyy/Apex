import {basicSetup} from 'codemirror';
import {EditorView, keymap} from '@codemirror/view';
import {EditorState, Compartment} from '@codemirror/state';
import {indentWithTab} from '@codemirror/commands';
import {python} from '@codemirror/lang-python';
import {javascript} from '@codemirror/lang-javascript';
import {html} from '@codemirror/lang-html';
import {css} from '@codemirror/lang-css';
import {json} from '@codemirror/lang-json';
import {markdown} from '@codemirror/lang-markdown';
import {sql} from '@codemirror/lang-sql';
import {Terminal} from '@xterm/xterm';
import {FitAddon} from '@xterm/addon-fit';

const languages = {python, js: () => javascript({jsx:true, typescript:true}), html, css, json, md:markdown, sql};
const theme = EditorView.theme({
  '&':{height:'100%',backgroundColor:'var(--field)',color:'var(--ink)',fontSize:'14px'},
  '.cm-scroller':{overflow:'auto',fontFamily:'var(--mono)',lineHeight:'1.6'},
  '.cm-content':{padding:'12px 0'},
  '.cm-gutters':{backgroundColor:'var(--bg2)',color:'var(--muted)',borderRight:'1px solid var(--line)'},
  '.cm-activeLine, .cm-activeLineGutter':{backgroundColor:'color-mix(in srgb,var(--a) 8%,transparent)'},
  '.cm-cursor':{borderLeftColor:'var(--a)'},
  '.cm-selectionBackground, &.cm-focused .cm-selectionBackground':{backgroundColor:'color-mix(in srgb,var(--a) 24%,transparent)'},
  '.cm-tooltip, .cm-panels':{backgroundColor:'var(--panel2)',color:'var(--ink)',borderColor:'var(--line2)'},
}, {dark:true});

window.ApexEditor = {
  create(parent, file, onChange, onSave) {
    const readOnly = file.state?.readOnly || new Compartment();
    const view = new EditorView({parent,state:file.state?.state || EditorState.create({doc:file.text,extensions:[
      basicSetup, theme, languages[file.lang]?.() || [],
      EditorView.contentAttributes.of({'aria-label':`Edit ${file.path}`,spellcheck:'false'}),
      keymap.of([{key:'Mod-s',run:()=>{onSave();return true;}},indentWithTab]),
      EditorView.updateListener.of(update=>{if(update.docChanged)onChange(update.state.doc.toString());}),
      readOnly.of(EditorState.readOnly.of(file.readonly || false)),
    ]})});
    return {value:()=>view.state.doc.toString(),snapshot:()=>({state:view.state,readOnly}),focus:()=>view.focus(),destroy:()=>view.destroy(),
      readonly:enabled=>view.dispatch({effects:readOnly.reconfigure(EditorState.readOnly.of(enabled))}),
      selectLine(line){const pos=view.state.doc.line(Math.max(1,Math.min(line,view.state.doc.lines))).from;view.dispatch({selection:{anchor:pos},effects:EditorView.scrollIntoView(pos,{y:'center'})});view.focus();}};
  }
};

window.ApexTerminal = {
  create(parent, onInput, onSize) {
    const term = new Terminal({cursorBlink:true,fontSize:13,fontFamily:'Consolas, "Cascadia Code", monospace',
      theme:{background:'#09101a',foreground:'#e7f2f8',cursor:'#66ccff',selectionBackground:'#23465c'}});
    const fit = new FitAddon();term.loadAddon(fit);term.open(parent);
    const dimensions = (rows,cols) => ({rows:Math.max(5,Math.min(200,rows)),cols:Math.max(10,Math.min(400,cols))});
    const data = term.onData(onInput);const size = term.onResize(({rows,cols})=>{const size=dimensions(rows,cols);onSize(size.rows,size.cols);});
    const observer = new ResizeObserver(()=>{if(parent.clientWidth&&parent.clientHeight)fit.fit();});observer.observe(parent);
    fit.fit();
    return {write:text=>term.write(text),reset:()=>term.reset(),focus:()=>term.focus(),
      size:()=>dimensions(term.rows,term.cols),
      destroy(){observer.disconnect();data.dispose();size.dispose();term.dispose();}};
  }
};
