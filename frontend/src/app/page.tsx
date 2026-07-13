'use client';

import { useState, useEffect } from 'react';

export default function Home() {
  const [prompt, setPrompt] = useState('');
  const [keys, setKeys] = useState({ openai: '', anthropic: '', gemini: '', glm: '' });
  const [provider, setProvider] = useState('gpt-4o');
  const [twilioSid, setTwilioSid] = useState('');
  const [twilioToken, setTwilioToken] = useState('');
  const [twilioNumber, setTwilioNumber] = useState('');
  const [status, setStatus] = useState<'idle' | 'saving' | 'success' | 'error'>('idle');
  const [message, setMessage] = useState('');
  const [loading, setLoading] = useState(true);

  useEffect(() => {
    const fetchPrompt = async () => {
      setKeys({
        openai: localStorage.getItem('whatsapp_openai_key') || '',
        anthropic: localStorage.getItem('whatsapp_anthropic_key') || '',
        gemini: localStorage.getItem('whatsapp_gemini_key') || '',
        glm: localStorage.getItem('whatsapp_glm_key') || ''
      });
      setProvider(localStorage.getItem('whatsapp_llm_provider') || 'gpt-4o');
      setTwilioSid(localStorage.getItem('whatsapp_twilio_sid') || '');
      setTwilioToken(localStorage.getItem('whatsapp_twilio_token') || '');
      setTwilioNumber(localStorage.getItem('whatsapp_twilio_number') || '');
      
      try {
        const res = await fetch('http://localhost:8005/api/settings/prompt');
        if (res.ok) {
          const data = await res.json();
          setPrompt(data.system_prompt || '');
        }
      } catch (e) {
        console.error("Failed to fetch prompt", e);
      } finally {
        setLoading(false);
      }
    };
    fetchPrompt();
  }, []);

  const handleSave = async (e: React.FormEvent) => {
    e.preventDefault();
    setStatus('saving');
    
    try {
      localStorage.setItem('whatsapp_openai_key', keys.openai);
      localStorage.setItem('whatsapp_anthropic_key', keys.anthropic);
      localStorage.setItem('whatsapp_gemini_key', keys.gemini);
      localStorage.setItem('whatsapp_glm_key', keys.glm);
      localStorage.setItem('whatsapp_llm_provider', provider);
      localStorage.setItem('whatsapp_twilio_sid', twilioSid);
      localStorage.setItem('whatsapp_twilio_token', twilioToken);
      localStorage.setItem('whatsapp_twilio_number', twilioNumber);

      await fetch('http://localhost:8005/api/settings/keys', {
        method: 'POST',
        headers: { 'Content-Type': 'application/json' },
        body: JSON.stringify({ 
          openai_api_key: keys.openai, 
          anthropic_api_key: keys.anthropic,
          gemini_api_key: keys.gemini,
          glm_api_key: keys.glm,
          llm_provider: provider,
          twilio_account_sid: twilioSid,
          twilio_auth_token: twilioToken,
          twilio_phone_number: twilioNumber
        }),
      });

      const res = await fetch('http://localhost:8005/api/settings/prompt', {
        method: 'POST',
        headers: { 'Content-Type': 'application/json' },
        body: JSON.stringify({ system_prompt: prompt }),
      });
      
      if (res.ok) {
        setStatus('success');
        setMessage('Agent personality updated successfully!');
        setTimeout(() => setStatus('idle'), 3000);
      } else {
        setStatus('error');
        setMessage('Failed to save settings.');
      }
    } catch (e) {
      console.error(e);
      setStatus('error');
      setMessage('Network error. Ensure backend is running.');
    }
  };

  return (
    <main className="dashboard-container">
      <div className="dashboard-header">
        <h1>WhatsApp AI Agent</h1>
        <p>Customize your agent's personality and instructions</p>
      </div>

      {loading ? (
        <p>Loading agent configuration...</p>
      ) : (
        <form onSubmit={handleSave}>
          <div className="form-group">
            <label htmlFor="prompt">System Prompt (Instructions)</label>
            <textarea 
              id="prompt"
              value={prompt}
              onChange={(e) => setPrompt(e.target.value)}
              placeholder="e.g. You are a helpful customer support bot for a shoe store..." 
              required
            />
          </div>
          <div className="form-group" style={{marginTop:'20px'}}>
            <label>OpenAI Key</label>
            <input type="password" value={keys.openai} onChange={(e)=>setKeys({...keys, openai: e.target.value})} />
          </div>
          <div className="form-group" style={{marginTop:'20px'}}>
            <label>Anthropic Key</label>
            <input type="password" value={keys.anthropic} onChange={(e)=>setKeys({...keys, anthropic: e.target.value})} />
          </div>
          <div className="form-group" style={{marginTop:'20px'}}>
            <label>Gemini Key</label>
            <input type="password" value={keys.gemini} onChange={(e)=>setKeys({...keys, gemini: e.target.value})} />
          </div>
          <div className="form-group" style={{marginTop:'20px'}}>
            <label>ZhipuAI Key</label>
            <input type="password" value={keys.glm} onChange={(e)=>setKeys({...keys, glm: e.target.value})} />
          </div>
          <div className="form-group" style={{marginTop:'20px'}}>
            <label>LLM Engine</label>
            <select value={provider} onChange={(e)=>setProvider(e.target.value)} style={{width: '100%', padding: '10px'}}>
              <option value="gpt-4o">OpenAI (gpt-4o)</option>
              <option value="claude-3-5-sonnet-20240620">Anthropic (claude-3-5-sonnet)</option>
              <option value="gemini/gemini-1.5-pro">Google AI (gemini-1.5-pro)</option>
              <option value="zhipu/glm-4">ZhipuAI (glm-4)</option>
            </select>
          </div>
          <div className="form-group" style={{marginTop:'20px'}}>
            <label>Twilio Account SID</label>
            <input type="password" value={twilioSid} onChange={(e)=>setTwilioSid(e.target.value)} placeholder="AC..." />
          </div>
          <div className="form-group" style={{marginTop:'20px'}}>
            <label>Twilio Auth Token</label>
            <input type="password" value={twilioToken} onChange={(e)=>setTwilioToken(e.target.value)} placeholder="..." />
          </div>
          <div className="form-group" style={{marginTop:'20px'}}>
            <label>Twilio Phone Number (WhatsApp)</label>
            <input type="text" value={twilioNumber} onChange={(e)=>setTwilioNumber(e.target.value)} placeholder="+1234567890" />
          </div>
          
          <button 
            type="submit"
            className="save-btn" 
            disabled={status === 'saving'}
            style={{marginTop:'20px'}}
          >
            {status === 'saving' ? 'Saving...' : 'Update Agent'}
          </button>
        </form>
      )}

      {status !== 'idle' && status !== 'saving' && (
        <div className={`status-message ${status}`}>
          {message}
        </div>
      )}
    </main>
  );
}
